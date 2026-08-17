from datetime import date
from unittest.mock import patch

from src.storage.models import Experience, Profile, Skill, VacancyRecord


def _seed_profile(session) -> Profile:
    profile = Profile(full_name="Test User", summary="Backend engineer.", email="t@example.test")
    profile.skills = [Skill(name="PHP", category="language")]
    profile.experiences = [
        Experience(
            title="Engineer", company="Acme", start_date=date(2020, 1, 1), end_date=None,
            highlights="Did stuff.",
        )
    ]
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def _seed_vacancy(session, **overrides) -> VacancyRecord:
    defaults = dict(
        source="adzuna", external_id="1", title="PHP Developer", company="Acme",
        url="https://example.test/1", description="Great job.",
    )
    record = VacancyRecord(**{**defaults, **overrides})
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def test_health_ok_when_mysql_and_es_reachable(client, session):
    with patch("src.main.get_es_client") as get_es_client:
        get_es_client.return_value.ping.return_value = True
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_503_when_es_unreachable(client, session):
    """Regression: an independent Codex review found /health previously
    returned ok regardless of MySQL/ES reachability, which could route
    traffic to an instance that can't actually serve anything."""
    with patch("src.main.get_es_client") as get_es_client:
        get_es_client.return_value.ping.return_value = False
        response = client.get("/health")

    assert response.status_code == 503
    assert "elasticsearch" in str(response.json())


def test_profile_404_when_not_seeded(client, session):
    response = client.get("/profile")
    assert response.status_code == 404


def test_profile_returns_seeded_data(client, session):
    _seed_profile(session)

    response = client.get("/profile")

    assert response.status_code == 200
    assert response.json()["full_name"] == "Test User"


def test_vacancies_filters_by_keyword(client, session):
    _seed_vacancy(session, title="PHP Developer", description="Symfony backend role.")
    _seed_vacancy(session, external_id="2", title="Frontend Developer", description="React role.")

    response = client.get("/vacancies", params={"keywords": "symfony"})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["vacancies"][0]["title"] == "PHP Developer"


def test_resume_requires_seeded_profile(client, session):
    response = client.get("/resume")
    assert response.status_code == 404


def test_resume_version_round_trip(client, session):
    _seed_profile(session)

    saved = client.post("/resume/versions", params={"label": "v1"})
    assert saved.status_code == 200
    version_id = saved.json()["id"]

    listed = client.get("/resume/versions")
    assert listed.status_code == 200
    assert any(v["id"] == version_id for v in listed.json())

    fetched = client.get(f"/resume/versions/{version_id}")
    assert fetched.status_code == 200
    assert "Test User" in fetched.text


def test_recommendations_carries_vacancy_id_through_vector_search(client, session):
    """Regression: an independent Codex review found _compute_recommendations
    keyed its shortlist/display lookup by vacancy url, so two postings
    sharing a url could conflate results - now keyed by real vacancy id
    end to end. This exercises the full /recommendations route, not just
    the unit-level VectorMatchResult/LlmMatchResult models."""
    _seed_profile(session)
    vacancy = _seed_vacancy(session)

    with patch("src.main.vector_search") as vector_search:
        vector_search.return_value = [{
            "vacancy_id": vacancy.id,
            "source": vacancy.source,
            "title": vacancy.title,
            "company": vacancy.company,
            "url": vacancy.url,
            "score": 0.9,
        }]
        response = client.get("/recommendations")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["recommendations"][0]["vacancy_id"] == vacancy.id


def test_recommendations_llm_rerank_receives_id_vacancy_pairs(client, session):
    _seed_profile(session)
    vacancy = _seed_vacancy(session)

    with patch("src.main.vector_search") as vector_search, patch("src.main.llm_rerank") as llm_rerank:
        vector_search.return_value = [{
            "vacancy_id": vacancy.id,
            "source": vacancy.source,
            "title": vacancy.title,
            "company": vacancy.company,
            "url": vacancy.url,
            "score": 0.9,
        }]
        llm_rerank.return_value = []

        response = client.get("/recommendations", params={"llm_rerank_top_n": 1})

    assert response.status_code == 200
    (shortlist, _profile_arg), _kwargs = llm_rerank.call_args
    assert len(shortlist) == 1
    shortlist_id, shortlist_vacancy = shortlist[0]
    assert shortlist_id == vacancy.id
    assert shortlist_vacancy.title == vacancy.title
    assert shortlist_vacancy.url == vacancy.url


def _vector_search_hit(vacancy):
    return [{
        "vacancy_id": vacancy.id,
        "source": vacancy.source,
        "title": vacancy.title,
        "company": vacancy.company,
        "url": vacancy.url,
        "score": 0.9,
    }]


def test_set_status_creates_application(client, session):
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved", "notes": "looks promising", "update_details": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    from src.storage.application_repo import get_application
    application = get_application(session, vacancy.id)
    assert application.status == "saved"
    assert application.notes == "looks promising"


def test_set_status_rejects_unknown_status(client, session):
    vacancy = _seed_vacancy(session)

    response = client.post(f"/vacancies/{vacancy.id}/status", data={"status": "ghosted"})

    assert response.status_code == 400


def test_set_status_404_for_unknown_vacancy(client, session):
    response = client.post("/vacancies/999999/status", data={"status": "saved"})
    assert response.status_code == 404


def test_quick_status_update_preserves_existing_notes_and_follow_up(client, session):
    """Regression: an independent Codex review found that the quick
    status-select on the recommendations page - which only ever submits
    `status`, matching what index.html's form actually sends - used to
    silently blank out notes/a follow-up date already set from the full
    edit form on /applications."""
    from datetime import date as date_cls

    from src.storage.application_repo import get_application, set_status

    vacancy = _seed_vacancy(session)
    set_status(
        session, vacancy.id, "applied", notes="applied via referral", follow_up_at=date_cls(2026, 9, 1)
    )

    response = client.post(
        f"/vacancies/{vacancy.id}/status", data={"status": "interviewing"}, follow_redirects=False
    )

    assert response.status_code == 303
    application = get_application(session, vacancy.id)
    assert application.status == "interviewing"
    assert application.notes == "applied via referral"
    assert application.follow_up_at == date_cls(2026, 9, 1)


def test_full_edit_form_can_explicitly_clear_notes_and_follow_up(client, session):
    """The other half: the /applications form always submits notes/
    follow_up_at (even blank), so an explicit clear there must still
    work - this isn't the same code path as the quick-select above."""
    from datetime import date as date_cls

    from src.storage.application_repo import get_application, set_status

    vacancy = _seed_vacancy(session)
    set_status(
        session, vacancy.id, "applied", notes="applied via referral", follow_up_at=date_cls(2026, 9, 1)
    )

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "applied", "notes": "", "follow_up_at": "", "update_details": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    application = get_application(session, vacancy.id)
    assert application.notes == ""
    assert application.follow_up_at is None


def test_malformed_follow_up_at_returns_422_not_500(client, session):
    """Regression: an independent Codex review found date.fromisoformat()
    was called unguarded - a malformed date crashed this into a raw 500
    instead of a client error."""
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved", "follow_up_at": "not-a-date", "update_details": "true"},
    )

    assert response.status_code == 422


def test_redirect_to_rejects_external_url(client, session):
    """Regression: an independent Codex review found redirect_to (a
    client-controlled hidden form field) was passed straight into
    RedirectResponse unvalidated - an open-redirect vector even for a
    localhost-only tool, since a malicious page could POST here with an
    off-site redirect_to. Off-site targets fall back to / instead of
    erroring, since a bad redirect_to shouldn't block a status update
    that already succeeded."""
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved", "redirect_to": "https://evil.example"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_redirect_to_rejects_protocol_relative_url(client, session):
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved", "redirect_to": "//evil.example"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_cross_origin_status_update_rejected(client, session):
    """Regression: an independent Codex review found this state-changing
    POST had no CSRF protection at all - loopback binding doesn't stop a
    page open in the user's own browser from submitting a form here, and
    predictable vacancy ids make a targeted status change feasible."""
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403


def test_same_origin_status_update_still_works(client, session):
    vacancy = _seed_vacancy(session)

    response = client.post(
        f"/vacancies/{vacancy.id}/status",
        data={"status": "saved"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_dismissed_vacancy_excluded_from_recommendations(client, session):
    """Regression: dismissing/rejecting a vacancy should stop it from
    reappearing in the semantic-match feed - otherwise "dismiss" doesn't
    actually do anything the user would notice."""
    _seed_profile(session)
    vacancy = _seed_vacancy(session)

    from src.storage.application_repo import set_status
    set_status(session, vacancy.id, "dismissed")

    with patch("src.main.vector_search") as vector_search:
        vector_search.return_value = _vector_search_hit(vacancy)
        response = client.get("/recommendations")

    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_saved_vacancy_still_shown_in_recommendations(client, session):
    """Only dismissed/rejected hide a vacancy - "saved" should stay
    visible (with its status surfaced), not disappear from the feed."""
    _seed_profile(session)
    vacancy = _seed_vacancy(session)

    from src.storage.application_repo import set_status
    set_status(session, vacancy.id, "saved")

    with patch("src.main.vector_search") as vector_search:
        vector_search.return_value = _vector_search_hit(vacancy)
        response = client.get("/recommendations")

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_applications_page_empty(client, session):
    response = client.get("/applications")
    assert response.status_code == 200
    assert "Nothing tracked yet" in response.text


def test_applications_page_shows_tracked_vacancy(client, session):
    vacancy = _seed_vacancy(session)
    from src.storage.application_repo import set_status
    set_status(session, vacancy.id, "applied", notes="via referral")

    response = client.get("/applications")

    assert response.status_code == 200
    assert vacancy.title in response.text
    assert "via referral" in response.text
