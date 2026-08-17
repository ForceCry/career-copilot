import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import src.storage.application_repo as application_repo  # noqa: E402
from src.ingestion.models import Vacancy  # noqa: E402
from src.storage.application_repo import (  # noqa: E402
    get_application,
    get_applications_map,
    get_excluded_companies,
    get_remote_feedback,
    get_seniority_feedback,
    get_skill_feedback,
    list_applications,
    primary_seniority_level,
    set_status,
    skill_mentioned,
)
from src.storage.models import Application  # noqa: E402
from src.storage.vacancy_repo import upsert_vacancies  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _vacancy(**overrides) -> Vacancy:
    defaults = dict(
        source="adzuna", external_id="123", title="PHP Developer",
        company="Acme", location="Warsaw", remote=False,
        url="https://example.test/123", description="Original description.",
    )
    return Vacancy(**{**defaults, **overrides})


def _seed_vacancy_id(session) -> int:
    _, _, ids = upsert_vacancies(session, [_vacancy()])
    return ids[0]


def test_no_application_until_a_status_is_set(session):
    vacancy_id = _seed_vacancy_id(session)
    assert get_application(session, vacancy_id) is None


def test_set_status_creates_application_and_event(session):
    vacancy_id = _seed_vacancy_id(session)

    application = set_status(session, vacancy_id, "saved")

    assert application.vacancy_id == vacancy_id
    assert application.status == "saved"
    assert len(application.events) == 1
    assert application.events[0].status == "saved"


def test_set_status_updates_in_place_and_appends_event(session):
    """Regression check for the roadmap's 'activity timeline' ask -
    Application always reflects only the current status, but every
    transition is preserved via ApplicationEvent (same append-only
    pattern as ResumeVersion)."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "saved")
    set_status(session, vacancy_id, "applied", notes="Applied via referral")
    application = set_status(session, vacancy_id, "interviewing")

    assert application.status == "interviewing"
    # still exactly one Application row, not one per status
    all_applications = list_applications(session)
    assert len(all_applications) == 1

    statuses_in_order = [e.status for e in sorted(application.events, key=lambda e: (e.created_at, e.id))]
    assert statuses_in_order == ["saved", "applied", "interviewing"]


def test_set_status_rejects_unknown_status(session):
    vacancy_id = _seed_vacancy_id(session)
    with pytest.raises(ValueError):
        set_status(session, vacancy_id, "ghosted")


def test_notes_and_follow_up_at_are_preserved_when_not_passed(session):
    """Regression: an independent Codex review found that a status-only
    call (the quick-select on the recommendations page, which never
    touches notes/follow_up_at at all) used to silently blank out both
    fields, because the old code had no way to tell "caller didn't
    mention this field" apart from "caller explicitly cleared it". Not
    passing notes/follow_up_at at all must leave whatever's already
    stored untouched."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied", notes="applied via referral", follow_up_at=date(2026, 9, 1))

    application = set_status(session, vacancy_id, "interviewing")

    assert application.notes == "applied via referral"
    assert application.follow_up_at == date(2026, 9, 1)


def test_follow_up_at_can_be_explicitly_cleared(session):
    """The other half of the UNSET distinction: passing follow_up_at=None
    explicitly (not just omitting the argument) does clear it - this is
    what the /applications full-edit form does when its date field is
    left blank."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied", follow_up_at=date(2026, 9, 1))

    application = set_status(session, vacancy_id, "interviewing", follow_up_at=None)

    assert application.follow_up_at is None


def test_list_applications_filters_by_status(session):
    v1 = _seed_vacancy_id(session)
    _, _, v2_ids = upsert_vacancies(session, [_vacancy(external_id="456")])
    v2 = v2_ids[0]
    set_status(session, v1, "saved")
    set_status(session, v2, "rejected")

    saved_only = list_applications(session, statuses=["saved"])
    rejected_only = list_applications(session, statuses=["rejected"])
    both = list_applications(session, statuses=["saved", "rejected"])

    assert [a.vacancy_id for a in saved_only] == [v1]
    assert [a.vacancy_id for a in rejected_only] == [v2]
    assert len(both) == 2


def test_get_applications_map_bulk_lookup(session):
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "dismissed")

    result = get_applications_map(session, [vacancy_id, 999])

    assert set(result.keys()) == {vacancy_id}
    assert result[vacancy_id].status == "dismissed"


def test_get_applications_map_empty_input(session):
    assert get_applications_map(session, []) == {}


def test_get_excluded_companies_empty_when_nothing_tracked(session):
    assert get_excluded_companies(session) == set()


def test_company_excluded_once_every_tracked_application_is_negative(session):
    _, _, ids = upsert_vacancies(
        session, [_vacancy(external_id="1", company="BadCo"), _vacancy(external_id="2", company="BadCo")]
    )
    set_status(session, ids[0], "dismissed")
    set_status(session, ids[1], "rejected")

    assert get_excluded_companies(session) == {"badco"}


def test_company_not_excluded_if_any_application_is_positive(session):
    """One dismissed posting isn't a verdict on the whole company - a
    saved/applied/interviewing/offer for the same company anywhere keeps
    it out of the exclusion set."""
    _, _, ids = upsert_vacancies(
        session, [_vacancy(external_id="1", company="MixedCo"), _vacancy(external_id="2", company="MixedCo")]
    )
    set_status(session, ids[0], "dismissed")
    set_status(session, ids[1], "applied")

    assert get_excluded_companies(session) == set()


def test_company_exclusion_lifts_after_a_later_positive_status(session):
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "dismissed")
    assert get_excluded_companies(session) == {"acme"}

    set_status(session, vacancy_id, "applied")

    assert get_excluded_companies(session) == set()


def test_company_exclusion_normalizes_case_and_whitespace(session):
    """Regression: an independent Codex review found company matching was
    an exact string comparison - "Acme", "ACME", and " Acme " (plausible
    across different sources, or the same source over time) would each be
    tracked as a separate company instead of being recognized as the same
    one."""
    _, _, ids = upsert_vacancies(
        session,
        [_vacancy(external_id="1", company="Acme"), _vacancy(external_id="2", company=" ACME ")],
    )
    set_status(session, ids[0], "dismissed")
    set_status(session, ids[1], "rejected")

    assert get_excluded_companies(session) == {"acme"}


def test_blank_company_name_never_excluded(session):
    """Regression: an independent Codex review found a blank/unknown
    company name ("") was being treated as a real, shared company key -
    dismissing one company-less posting would then hide every OTHER
    company-less posting too, since an absent company name isn't evidence
    two postings are from the same employer."""
    _, _, ids = upsert_vacancies(
        session, [_vacancy(external_id="1", company=""), _vacancy(external_id="2", company="  ")]
    )
    set_status(session, ids[0], "dismissed")
    set_status(session, ids[1], "rejected")

    assert get_excluded_companies(session) == set()


def test_skill_mentioned_rejects_short_skill_inside_an_unrelated_word(session):
    """Regression: an independent Codex review found plain `skill in
    haystack` let a short skill name match inside ordinary words - "go"
    is a substring of "good", "r" is a substring of "career", neither
    mention has anything to do with the Go or R programming languages."""
    assert skill_mentioned("go", "looking for a good backend developer") is False
    assert skill_mentioned("r", "join our career team") is False


def test_skill_mentioned_matches_a_standalone_short_skill(session):
    assert skill_mentioned("go", "we need a go developer") is True
    assert skill_mentioned("go", "experience with go.") is True
    assert skill_mentioned("go", "(go/golang) role") is True


def test_skill_mentioned_matches_symbol_suffixed_skills(session):
    """Not a regex \\b check - that fails here, since \\b never fires
    between a trailing symbol and following whitespace (neither side is a
    \\w character)."""
    assert skill_mentioned("c++", "looking for a c++ developer") is True
    assert skill_mentioned("c#", "c#/.net backend role") is True


def test_skill_feedback_empty_with_no_skill_names(session):
    assert get_skill_feedback(session, []) == {}


def test_skill_feedback_empty_below_min_sample_size(session):
    """Two dismissed postings mentioning a skill isn't enough to call it
    signal - a lone/pair of dismissals could be for any unrelated reason."""
    _, _, ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="Looking for a WordPress expert."),
            _vacancy(external_id="2", description="WordPress and PHP required."),
        ],
    )
    for vid in ids:
        set_status(session, vid, "dismissed")

    assert get_skill_feedback(session, ["WordPress"]) == {}


def test_skill_feedback_negative_when_dismissed_disproportionately_more_than_baseline(session):
    """Not just "mostly dismissed" - dismissed at a rate meaningfully
    HIGHER than the user's overall dismiss rate. Without a baseline-
    relative comparison, a skill mentioned in every tracked posting would
    just mirror however lopsided the user's overall accept/reject rate
    happens to be, which isn't skill-specific signal at all."""
    _, _, negative_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="Looking for a WordPress expert."),
            _vacancy(external_id="2", description="WordPress and PHP required."),
            _vacancy(external_id="3", description="Senior WordPress developer wanted."),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", description="Generic backend role."),
            _vacancy(external_id="5", description="Another generic backend role."),
        ],
    )
    for vid in negative_ids:
        set_status(session, vid, "dismissed")
    for vid in baseline_ids:
        set_status(session, vid, "applied")

    # baseline dismiss rate: 3/5 = 0.6; wordpress-specific: 3/3 = 1.0 -
    # a 0.4 gap, over the 0.3 margin.
    assert get_skill_feedback(session, ["WordPress"]) == {"wordpress": -10}


def test_remote_feedback_empty_with_no_applications(session):
    assert get_remote_feedback(session) == {}


def test_remote_feedback_never_flags_onsite_even_when_dismissed_above_baseline(session):
    """Regression: an independent Codex review found remote=False was
    treated as a confirmed "onsite" signal, but it's also this codebase's
    "unknown" value - src/ingestion/sources/adzuna.py hard-codes
    remote=False for every vacancy since Adzuna's API doesn't expose that
    field at all. Even a clean, textbook "dismissed disproportionately
    more than baseline" pattern on remote=False rows must never produce
    an "onsite" key - there's no way to tell a genuinely-onsite dismissal
    apart from an unknown-status one from this field alone."""
    _, _, onsite_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", remote=False),
            _vacancy(external_id="2", remote=False),
            _vacancy(external_id="3", remote=False),
        ],
    )
    _, _, remote_ids = upsert_vacancies(
        session,
        [_vacancy(external_id="4", remote=True), _vacancy(external_id="5", remote=True)],
    )
    for vid in onsite_ids:
        set_status(session, vid, "dismissed")
    for vid in remote_ids:
        set_status(session, vid, "applied")

    assert get_remote_feedback(session) == {}


def test_remote_feedback_positive_when_remote_engaged_above_baseline(session):
    _, _, remote_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", remote=True),
            _vacancy(external_id="2", remote=True),
            _vacancy(external_id="3", remote=True),
        ],
    )
    _, _, onsite_ids = upsert_vacancies(
        session,
        [_vacancy(external_id="4", remote=False), _vacancy(external_id="5", remote=False)],
    )
    for vid in remote_ids:
        set_status(session, vid, "applied")
    for vid in onsite_ids:
        set_status(session, vid, "dismissed")

    assert get_remote_feedback(session) == {"remote": 10}


def test_remote_feedback_no_signal_when_dismiss_rate_matches_baseline(session):
    """Both categories individually dismissed at exactly the same rate as
    the overall baseline (2/3 each) - neither is disproportionately worse,
    so neither counts as signal, even though each has enough samples to
    otherwise clear FEEDBACK_MIN_SAMPLES."""
    _, _, remote_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", remote=True),
            _vacancy(external_id="2", remote=True),
            _vacancy(external_id="3", remote=True),
        ],
    )
    _, _, onsite_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", remote=False),
            _vacancy(external_id="5", remote=False),
            _vacancy(external_id="6", remote=False),
        ],
    )
    for status, vid in zip(["dismissed", "dismissed", "applied"], remote_ids):
        set_status(session, vid, status)
    for status, vid in zip(["dismissed", "dismissed", "applied"], onsite_ids):
        set_status(session, vid, status)

    assert get_remote_feedback(session) == {}


def test_primary_seniority_level_picks_the_earliest_match(session):
    """Regression: an independent Codex review found a title matching
    multiple SENIORITY_LEVELS keywords (e.g. "Senior Lead Engineer") used
    to contribute to BOTH buckets - a posting is one seniority level, not
    several. primary_seniority_level picks whichever keyword appears
    first in the title, matching how titles conventionally lead with
    their primary qualifier."""
    assert primary_seniority_level("senior lead engineer") == "senior"
    assert primary_seniority_level("lead / senior engineer") == "lead"
    assert primary_seniority_level("backend developer") is None


def test_seniority_feedback_does_not_double_count_a_title_matching_two_levels(session):
    _, _, negative_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", title="Senior Lead Engineer"),
            _vacancy(external_id="2", title="Senior Lead Architect"),
            _vacancy(external_id="3", title="Senior Lead Developer"),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", title="Backend Engineer"),
            _vacancy(external_id="5", title="Backend Role"),
        ],
    )
    for vid in negative_ids:
        set_status(session, vid, "dismissed")
    for vid in baseline_ids:
        set_status(session, vid, "applied")

    # "senior" comes first in each title, so it's the only bucket that
    # gets these 3 dismissals - "lead" must stay empty (no signal), not
    # also pick up -10 from the same 3 postings.
    assert get_seniority_feedback(session) == {"senior": -10}


def test_seniority_feedback_negative_when_a_level_dismissed_above_baseline(session):
    _, _, junior_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", title="Junior PHP Developer"),
            _vacancy(external_id="2", title="Junior Backend Engineer"),
            _vacancy(external_id="3", title="Junior Developer role"),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", title="Backend Engineer"),
            _vacancy(external_id="5", title="Backend Role"),
        ],
    )
    for vid in junior_ids:
        set_status(session, vid, "dismissed")
    for vid in baseline_ids:
        set_status(session, vid, "applied")

    assert get_seniority_feedback(session) == {"junior": -10}


def test_seniority_feedback_ignores_incidental_mention_outside_title(session):
    """Only the title is searched - "senior" appearing in a description
    (e.g. "reports to a senior manager") on an otherwise-unrelated-level
    posting shouldn't count as a senior-level data point. Constructed so
    that searching the full text WOULD produce a (wrong) signal here -
    "senior" only appears in the 3 dismissed postings' descriptions, never
    in the 2 baseline ones - so this only stays {} if description text is
    correctly excluded."""
    _, _, dismissed_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", title="Backend Developer", description="Reports to a senior manager."),
            _vacancy(external_id="2", title="Backend Developer", description="Reports to a senior manager."),
            _vacancy(external_id="3", title="Backend Developer", description="Reports to a senior manager."),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", title="Backend Developer", description="Generic role."),
            _vacancy(external_id="5", title="Backend Developer", description="Generic role."),
        ],
    )
    for vid in dismissed_ids:
        set_status(session, vid, "dismissed")
    for vid in baseline_ids:
        set_status(session, vid, "applied")

    assert get_seniority_feedback(session) == {}


def test_skill_feedback_ignores_short_skill_matched_inside_unrelated_word(session):
    """End-to-end version of the skill_mentioned regression above: three
    dismissed postings that happen to say "good fit" shouldn't produce a
    negative signal for a "Go" skill just because "go" is a substring of
    "good"."""
    _, _, negative_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="Looking for a good backend developer."),
            _vacancy(external_id="2", description="Would be a good culture fit."),
            _vacancy(external_id="3", description="Good opportunity, PHP required."),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", description="Generic backend role."),
            _vacancy(external_id="5", description="Another generic backend role."),
        ],
    )
    for vid in negative_ids:
        set_status(session, vid, "dismissed")
    for vid in baseline_ids:
        set_status(session, vid, "applied")

    assert get_skill_feedback(session, ["Go"]) == {}


def test_skill_feedback_negative_signal_does_not_fire_for_a_ubiquitous_skill(session):
    """Regression for the bug caught while designing this function: a
    skill mentioned in EVERY tracked posting (e.g. the user's own core
    stack, present in nearly every job title in their search results)
    will get dismissed at roughly the same rate as everything else,
    simply because it's everywhere - that must NOT be flagged as
    negative feedback just because the user's overall dismiss rate is
    high."""
    _, _, ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="PHP role one."),
            _vacancy(external_id="2", description="PHP role two."),
            _vacancy(external_id="3", description="PHP role three."),
            _vacancy(external_id="4", description="PHP role four."),
        ],
    )
    # 80% dismissed overall - a high bar that would trip the old absolute
    # threshold, but PHP is mentioned in ALL four, so it's exactly at
    # baseline, not above it.
    for status, vid in zip(["dismissed", "dismissed", "dismissed", "applied"], ids):
        set_status(session, vid, status)

    assert get_skill_feedback(session, ["PHP"]) == {}


def test_skill_feedback_positive_when_engaged_disproportionately_more_than_baseline(session):
    _, _, positive_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="Symfony backend role."),
            _vacancy(external_id="2", description="We use Symfony 6 heavily."),
            _vacancy(external_id="3", description="Symfony + API Platform team."),
        ],
    )
    _, _, baseline_ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="4", description="Generic backend role."),
            _vacancy(external_id="5", description="Another generic backend role."),
        ],
    )
    for status, vid in zip(["saved", "applied", "interviewing"], positive_ids):
        set_status(session, vid, status)
    for vid in baseline_ids:
        set_status(session, vid, "dismissed")

    # baseline dismiss rate: 2/5 = 0.4; symfony-specific: 0/3 = 0.0 - a
    # 0.4 gap, over the 0.3 margin.
    assert get_skill_feedback(session, ["Symfony"]) == {"symfony": 10}


def test_skill_feedback_no_signal_when_outcomes_are_mixed(session):
    _, _, ids = upsert_vacancies(
        session,
        [
            _vacancy(external_id="1", description="Laravel developer wanted."),
            _vacancy(external_id="2", description="Laravel and Vue."),
            _vacancy(external_id="3", description="Laravel API role."),
            _vacancy(external_id="4", description="Laravel monolith."),
        ],
    )
    for status, vid in zip(["dismissed", "applied", "dismissed", "applied"], ids):
        set_status(session, vid, status)

    assert get_skill_feedback(session, ["Laravel"]) == {}


def test_set_status_recovers_from_concurrent_create_race(session, monkeypatch):
    """Regression: an independent Codex review found that two concurrent
    first-time status changes on the same vacancy could both see "no
    application yet" (the unique constraint on vacancy_id is only
    enforced at flush, not at the read in set_status), then race to
    INSERT - the loser used to surface a raw IntegrityError/500 instead
    of falling back to updating the row the winner had already committed.
    get_application is mocked to miss exactly once, simulating that race
    window, while the "concurrent" row is genuinely committed first."""
    vacancy_id = _seed_vacancy_id(session)

    real_get_application = application_repo.get_application
    calls = {"n": 0}

    def flaky_get_application(s, v):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_get_application(s, v)

    monkeypatch.setattr(application_repo, "get_application", flaky_get_application)

    session.add(Application(vacancy_id=vacancy_id, status="saved"))
    session.commit()

    application = application_repo.set_status(session, vacancy_id, "applied")

    assert application.status == "applied"
    assert len(application_repo.list_applications(session)) == 1
    # the "concurrent" row was seeded directly (bypassing set_status), so
    # only the applied transition this call made has an event
    assert [e.status for e in application.events] == ["applied"]


def test_no_event_recorded_when_status_is_unchanged(session):
    """Regression: an independent Codex review found every call appended
    an ApplicationEvent even when the status didn't actually change -
    editing just the notes/follow-up date from /applications produced
    misleading "applied -> applied" entries in what's meant to be a
    transition history, not a general edit log."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied")

    application = set_status(session, vacancy_id, "applied", notes="just editing the note")

    assert application.notes == "just editing the note"
    assert len(application.events) == 1  # not 2
