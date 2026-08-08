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
