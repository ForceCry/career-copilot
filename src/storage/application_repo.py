from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .models import (
    APPLICATION_STATUSES,
    NEGATIVE_APPLICATION_STATUSES,
    Application,
    ApplicationEvent,
    VacancyRecord,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Distinguishes "caller didn't submit this field, leave it alone" from
# "caller explicitly submitted an empty/None value, clear it" - plain
# None can't do this since follow_up_at's own legitimate value IS None
# (no follow-up date set). Real bug, caught by an independent Codex
# review: the quick status-select on the recommendations page only
# submits `status`, not notes/follow_up_at - without this sentinel,
# set_status's old always-overwrite behavior silently erased any notes
# or follow-up date someone had already set from the /applications page.
UNSET: Any = object()


def get_application(session: Session, vacancy_id: int) -> Application | None:
    return session.exec(select(Application).where(Application.vacancy_id == vacancy_id)).first()


def get_applications_map(session: Session, vacancy_ids: list[int]) -> dict[int, Application]:
    """Bulk lookup for joining current status onto a list of vacancies
    (recommendations, /vacancies) without a query per row."""
    if not vacancy_ids:
        return {}
    rows = session.exec(select(Application).where(Application.vacancy_id.in_(vacancy_ids))).all()
    return {a.vacancy_id: a for a in rows}


def set_status(
    session: Session,
    vacancy_id: int,
    status: str,
    notes: str = UNSET,
    follow_up_at: date | None = UNSET,
) -> Application:
    """Creates the Application row on first status change for a vacancy,
    updates it in place on every later one - Application always reflects
    the CURRENT status, ApplicationEvent is what preserves the transition
    history. notes/follow_up_at are a partial update: passing UNSET
    (the default) leaves whatever's already stored untouched, so a caller
    that only cares about status - the quick-select on the
    recommendations page - can't silently wipe notes/a follow-up date set
    from the /applications page. Pass an explicit "" or None to clear
    either field on purpose.

    Only appends an ApplicationEvent when the status actually changes -
    editing just the notes or follow-up date on an unchanged status
    doesn't get its own history entry (no "applied -> applied" noise),
    since ApplicationEvent is meant to be a transition log, not a general
    edit log. Both of these were confirmed as real bugs by an independent
    Codex review, not just theoretical."""
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"Unknown status {status!r} - must be one of {APPLICATION_STATUSES}")

    now = _utcnow()
    application = get_application(session, vacancy_id)
    just_created = False

    if application is None:
        application = Application(
            vacancy_id=vacancy_id,
            status=status,
            notes="" if notes is UNSET else notes,
            follow_up_at=None if follow_up_at is UNSET else follow_up_at,
            created_at=now,
            updated_at=now,
        )
        session.add(application)
        try:
            session.flush()  # assigns application.id before the event references it
            just_created = True
        except IntegrityError:
            # Two concurrent first-time status changes on the same vacancy
            # can both see "no application yet" here - the unique
            # constraint on vacancy_id is only enforced at flush, not at
            # the read above. Whoever loses the race falls back to
            # updating the row the winner just committed, instead of
            # surfacing a raw 500. Flagged by an independent Codex review.
            session.rollback()
            application = get_application(session, vacancy_id)

    status_changed = just_created or application.status != status
    event_note = "" if notes is UNSET else notes

    if not just_created:
        application.status = status
        if notes is not UNSET:
            application.notes = notes
        if follow_up_at is not UNSET:
            application.follow_up_at = follow_up_at
        application.updated_at = now
        session.add(application)
        session.flush()

    if status_changed:
        session.add(
            ApplicationEvent(application_id=application.id, status=status, note=event_note, created_at=now)
        )
    session.commit()
    session.refresh(application)
    return application


def list_applications(session: Session, statuses: list[str] | None = None) -> list[Application]:
    query = select(Application)
    if statuses:
        query = query.where(Application.status.in_(statuses))
    return session.exec(query.order_by(Application.updated_at.desc())).all()


def normalize_company_name(name: str) -> str:
    """Same real-world company can arrive with different casing/whitespace
    across sources (or even the same source over time) - "Acme", "ACME",
    " Acme " should all match for exclusion purposes. Callers on both
    sides of the exclusion check (get_excluded_companies below, and
    _company_not_excluded in main.py) must normalize through this same
    function, or the comparison silently stops working. Flagged by an
    independent Codex review."""
    return name.strip().casefold()


def get_excluded_companies(session: Session) -> set[str]:
    """Explicit feedback signal for recommendations: a company is excluded
    once every application tracked for it ended up dismissed/rejected -
    not the instant a single posting does, since one bad-fit role isn't a
    verdict on the whole company. A single saved/applied/interviewing/
    offer anywhere for that company keeps it out of this set (and pulls
    it back out automatically if the user later engages positively with a
    company they'd previously written off elsewhere).

    Returns normalize_company_name()'d keys. Blank/whitespace-only company
    names are never included even if every application against them is
    negative - an absent company name isn't evidence two postings belong
    to the same employer, and treating "" as a shared key would hide
    every future company-less posting after just one dismissal. Flagged
    by an independent Codex review.

    Reads every tracked application, not just ones touching the current
    candidate hit set - one join query, materialized and grouped in
    Python, not a SQL GROUP BY/HAVING. An independent Codex review flagged
    this as something that scales with total application history rather
    than the ~100-row hit set a single request actually needs; deliberately
    left as-is for now - a single-user tool's application table isn't
    going to reach a size where that matters, and scoping the query to the
    current hit set would couple this function's signature to its one
    caller for a cost saving that doesn't exist yet."""
    rows = session.exec(
        select(VacancyRecord.company, Application.status).join(
            Application, Application.vacancy_id == VacancyRecord.id
        )
    ).all()
    statuses_by_company: dict[str, set[str]] = defaultdict(set)
    for company, status in rows:
        key = normalize_company_name(company)
        if not key:
            continue
        statuses_by_company[key].add(status)
    return {
        company
        for company, statuses in statuses_by_company.items()
        if statuses and statuses <= NEGATIVE_APPLICATION_STATUSES
    }
