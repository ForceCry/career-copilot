from collections import defaultdict
from collections.abc import Callable, Iterable
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


# A category (skill, remote/onsite, seniority level, ...) only counts as
# signal once it's shown up across enough tracked decisions - one
# dismissed posting that happens to mention "Docker" says nothing about
# Docker itself (dozens of unrelated reasons could explain a single
# dismissal), so a lone data point is noise, not feedback. This is a much
# weaker signal than company exclusion (where "every posting from this
# company" is a clean, direct verdict on the company) - these are just
# attributes a posting happens to have, so this needs both a minimum
# sample size and a lopsided majority before treating it as real signal.
FEEDBACK_MIN_SAMPLES = 3
# A category's own dismiss rate must clear the user's OVERALL dismiss rate
# by this much (in either direction) before it counts as signal - not an
# absolute threshold. Caught empirically while writing get_skill_feedback's
# own tests (the first user of this logic): a candidate's core/primary
# skill (their whole job search is built around it, so it's mentioned in
# nearly every posting's title) will get dismissed at roughly the SAME
# rate as everything else, simply because it's ubiquitous - an absolute
# "80% of postings mentioning X got dismissed" threshold would spuriously
# flag it as negative feedback the moment the user's overall dismiss rate
# crosses 80% too, systematically deprioritizing their own specialty.
# Comparing against the baseline instead only flags a category that's
# dismissed (or engaged with) disproportionately MORE than everything
# else - applies equally to skills, remote/onsite, and seniority level,
# since all three can suffer the same "ubiquitous in this candidate's
# whole search" problem (e.g. someone only ever searching remote roles).
FEEDBACK_SIGNAL_MARGIN = 0.3
FEEDBACK_SCORE_ADJUSTMENT = 10


def skill_mentioned(skill: str, haystack: str) -> bool:
    """Whole-word(ish) match, not plain substring - flagged by an
    independent Codex review: bare `skill in haystack` let a short skill
    name like "Go", "R", or "C" match inside ordinary words ("good",
    "career", "backend"), producing skill feedback that has nothing to do
    with the skill itself. `skill` and `haystack` must both already be
    lowercased by the caller (this just walks raw string offsets, no
    normalization here). Deliberately NOT a regex \\b word-boundary check:
    that breaks for symbol-suffixed skills like "C++" or "C#" - \\b only
    fires at a transition between a word char and a non-word char, and
    there's no such transition between a trailing symbol and following
    whitespace/punctuation (both already non-word), so `\\bC\\+\\+\\b`
    fails to match "C++ developer". Checking that the char immediately
    before/after the match isn't itself alphanumeric sidesteps that
    entirely - punctuation and symbols on either side are always fine."""
    start = 0
    while True:
        idx = haystack.find(skill, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not haystack[idx - 1].isalnum()
        after_idx = idx + len(skill)
        after_ok = after_idx == len(haystack) or not haystack[after_idx].isalnum()
        if before_ok and after_ok:
            return True
        start = idx + 1


def _baseline_relative_feedback(
    rows: list[tuple], category_membership: Callable[[tuple], Iterable[str]]
) -> dict[str, int]:
    """Shared statistical core behind get_skill_feedback, get_remote_feedback
    and get_seniority_feedback below: for each category a tracked row
    belongs to (category_membership may return zero, one, or several -
    e.g. a title can match both "senior" and "lead"), compares that
    category's dismissed/rejected rate against the OVERALL dismissed rate
    across every row passed in - see FEEDBACK_SIGNAL_MARGIN's comment for
    why a fixed threshold instead of baseline-relative doesn't work. Each
    row's last element must be the Application.status string. Needs at
    least FEEDBACK_MIN_SAMPLES rows in a category before drawing any
    conclusion about it."""
    if not rows:
        return {}
    negative_rows = sum(1 for row in rows if row[-1] in NEGATIVE_APPLICATION_STATUSES)
    baseline_negative_ratio = negative_rows / len(rows)

    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"positive": 0, "negative": 0})
    for row in rows:
        outcome = "negative" if row[-1] in NEGATIVE_APPLICATION_STATUSES else "positive"
        for category in category_membership(row):
            counts[category][outcome] += 1

    feedback: dict[str, int] = {}
    for category, outcome_counts in counts.items():
        total = outcome_counts["positive"] + outcome_counts["negative"]
        if total < FEEDBACK_MIN_SAMPLES:
            continue
        negative_ratio = outcome_counts["negative"] / total
        if negative_ratio - baseline_negative_ratio >= FEEDBACK_SIGNAL_MARGIN:
            feedback[category] = -FEEDBACK_SCORE_ADJUSTMENT
        elif baseline_negative_ratio - negative_ratio >= FEEDBACK_SIGNAL_MARGIN:
            feedback[category] = FEEDBACK_SCORE_ADJUSTMENT
    return feedback


def get_skill_feedback(session: Session, skill_names: list[str]) -> dict[str, int]:
    """Explicit feedback signal for recommendations: for each of the
    candidate's own profile skills, look at every tracked application
    whose vacancy text mentions that skill, and see whether those
    decisions lean dismissed/rejected disproportionately more or less
    than the user's overall dismiss rate. This is a soft re-ranking nudge
    applied to vector-search scores, not a hard filter like
    get_excluded_companies, since the signal is inherently noisier."""
    if not skill_names:
        return {}
    rows = session.exec(
        select(VacancyRecord.title, VacancyRecord.description, VacancyRecord.tags, Application.status).join(
            Application, Application.vacancy_id == VacancyRecord.id
        )
    ).all()
    normalized_skills = [s.strip().lower() for s in skill_names if s.strip()]

    def _categories(row: tuple[str, str, str, str]) -> list[str]:
        title, description, tags, _status = row
        haystack = f"{title} {description} {tags}".lower()
        return [skill for skill in normalized_skills if skill_mentioned(skill, haystack)]

    return _baseline_relative_feedback(rows, _categories)


def get_remote_feedback(session: Session) -> dict[str, int]:
    """Same idea as get_skill_feedback, applied to the vacancy's own
    remote flag instead of a text mention - do remote postings get
    dismissed disproportionately more than the user's overall dismiss
    rate? Only ever returns a "remote" key, never "onsite" -
    VacancyRecord.remote=False is NOT a reliable "confirmed onsite"
    signal, flagged by an independent Codex review: Adzuna's source
    adapter (src/ingestion/sources/adzuna.py) hard-codes remote=False for
    every vacancy regardless of the posting's actual status, because
    Adzuna's API doesn't expose that field at all. Treating False as
    "onsite" would silently mix genuinely onsite postings with ones where
    remote status was simply never known, and Adzuna is one of three
    ingestion sources feeding this table. Only remote=True is trustworthy
    (Arbeitnow/JustJoinIt assert it positively when true), so this can
    only ever learn a preference FOR remote, never against it."""
    rows = session.exec(
        select(VacancyRecord.remote, Application.status).join(
            Application, Application.vacancy_id == VacancyRecord.id
        )
    ).all()

    def _categories(row: tuple[bool, str]) -> list[str]:
        remote, _status = row
        return ["remote"] if remote else []

    return _baseline_relative_feedback(rows, _categories)


# Deliberately just a fixed keyword list matched against the title, not an
# attempt at a general "desired role" taxonomy (backend/frontend/fullstack,
# domain, etc.) - those categories are far fuzzier to define from raw
# posting text than a handful of well-known seniority words, and without
# any real tracked applications yet to validate against, a fuzzier
# category is more likely to just be wrong. Ordered longest-prefix-first
# only for readability; skill_mentioned's boundary check means match order
# doesn't affect correctness (e.g. "middle" matching doesn't block "mid").
#
# An independent Codex review also found this fixed list can match
# unrelated compound job titles from other fields entirely - "mid" inside
# "Mid-Market Account Executive", "lead" inside "Lead Generation
# Specialist" (skill_mentioned's punctuation/space boundary check accepts
# both, since neither char adjacent to the match is alphanumeric).
# Deliberately NOT patched further: those titles wouldn't realistically
# appear in THIS candidate's results in the first place - recommendations
# are already filtered down to whatever vector search judges semantically
# similar to this specific candidate's own profile before any of this
# feedback logic ever runs, so a sales/marketing title landing in a
# backend developer's candidate pool is an edge case this tool's own
# upstream filtering already mostly rules out.
SENIORITY_LEVELS = (
    "intern", "junior", "mid", "middle", "senior", "lead", "staff", "principal", "head",
)


def primary_seniority_level(title_lower: str) -> str | None:
    """A posting is one seniority level, not several - "Senior Lead
    Engineer" shouldn't train (or later apply) both the senior and lead
    buckets at once. Picks whichever SENIORITY_LEVELS keyword appears
    EARLIEST in the (already-lowercased) title, matching the convention
    that job titles lead with their primary qualifier. Flagged by an
    independent Codex review: the original version collected every
    matching level and let them all contribute, so a single title could
    produce two (possibly conflicting) adjustments."""
    matches = [
        (title_lower.find(level), level) for level in SENIORITY_LEVELS if skill_mentioned(level, title_lower)
    ]
    if not matches:
        return None
    return min(matches)[1]


def get_seniority_feedback(session: Session) -> dict[str, int]:
    """Same idea as get_skill_feedback, applied to the single
    primary_seniority_level() found in the vacancy TITLE only (not
    description/tags - title is where postings conventionally state
    seniority; searching the full text would risk matching incidental
    mentions like "reports to a senior manager" on an unrelated-level
    posting)."""
    rows = session.exec(
        select(VacancyRecord.title, Application.status).join(
            Application, Application.vacancy_id == VacancyRecord.id
        )
    ).all()

    def _categories(row: tuple[str, str]) -> list[str]:
        title, _status = row
        level = primary_seniority_level(title.lower())
        return [level] if level else []

    return _baseline_relative_feedback(rows, _categories)
