import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from src.ingestion.models import Vacancy  # noqa: E402
from src.storage.artifact_repo import (  # noqa: E402
    list_artifacts_for_vacancies,
    list_artifacts_for_vacancy,
    save_artifact,
)
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


def _seed_vacancy_id(session, **overrides) -> int:
    _, _, ids = upsert_vacancies(session, [_vacancy(**overrides)])
    return ids[0]


def _save(session, vacancy_id, artifact_type, content, **overrides):
    defaults = dict(vacancy_title="PHP Developer", vacancy_company="Acme", vacancy_url="https://example.test/123")
    kwargs = {**defaults, **overrides}
    return save_artifact(session, vacancy_id, artifact_type, content, **kwargs)


def test_save_artifact_rejects_unknown_type(session):
    vacancy_id = _seed_vacancy_id(session)
    with pytest.raises(ValueError):
        _save(session, vacancy_id, "resume_pdf", "content")


def test_save_and_list_artifacts_for_vacancy(session):
    vacancy_id = _seed_vacancy_id(session)

    _save(session, vacancy_id, "cover_letter", "Dear hiring manager...")
    _save(session, vacancy_id, "tailoring_suggestions", "- Emphasize X")

    artifacts = list_artifacts_for_vacancy(session, vacancy_id)
    assert len(artifacts) == 2
    assert {a.artifact_type for a in artifacts} == {"cover_letter", "tailoring_suggestions"}


def test_artifact_snapshots_vacancy_fields_at_generation_time(session):
    """Regression: an independent Codex review found artifacts used to be
    rendered against the CURRENT VacancyRecord instead of a snapshot -
    re-ingestion overwrites title/company/url in place, so a later edit
    to the posting would silently change what an old letter appears to
    be about. save_artifact now takes an explicit snapshot instead of
    deriving it from a live lookup."""
    vacancy_id = _seed_vacancy_id(session)

    artifact = _save(
        session, vacancy_id, "cover_letter", "Dear hiring manager...",
        vacancy_title="Original Title", vacancy_company="Original Co", vacancy_url="https://example.test/orig",
    )

    # simulate the posting being re-ingested/edited after the artifact
    # was generated
    _seed_vacancy_id(session, title="Edited Title", company="Edited Co", url="https://example.test/edited")

    assert artifact.vacancy_title == "Original Title"
    assert artifact.vacancy_company == "Original Co"
    assert artifact.vacancy_url == "https://example.test/orig"


def test_regenerating_appends_rather_than_overwrites(session):
    """Same "never overwritten in place" pattern as ResumeVersion - a
    second cover letter generation for the same vacancy shouldn't erase
    the first one someone might still want to compare against."""
    vacancy_id = _seed_vacancy_id(session)

    _save(session, vacancy_id, "cover_letter", "First draft")
    _save(session, vacancy_id, "cover_letter", "Second draft")

    artifacts = list_artifacts_for_vacancy(session, vacancy_id)
    assert len(artifacts) == 2
    assert [a.content for a in artifacts] == ["Second draft", "First draft"]  # newest first


def test_list_artifacts_for_vacancy_empty_when_none_generated(session):
    vacancy_id = _seed_vacancy_id(session)
    assert list_artifacts_for_vacancy(session, vacancy_id) == []


def test_list_artifacts_for_vacancies_bulk_lookup(session):
    _, _, ids = upsert_vacancies(
        session, [_vacancy(external_id="1"), _vacancy(external_id="2"), _vacancy(external_id="3")]
    )
    _save(session, ids[0], "cover_letter", "Letter for vacancy 1")
    _save(session, ids[1], "tailoring_suggestions", "Suggestions for vacancy 2")
    # ids[2] has no artifacts at all

    result = list_artifacts_for_vacancies(session, ids)

    assert set(result.keys()) == {ids[0], ids[1]}
    # lightweight summaries only - id/vacancy_id/artifact_type/created_at,
    # not the full content (see ArtifactSummary's docstring)
    assert result[ids[0]][0].artifact_type == "cover_letter"
    assert not hasattr(result[ids[0]][0], "content")
    assert ids[2] not in result


def test_list_artifacts_for_vacancies_empty_input(session):
    assert list_artifacts_for_vacancies(session, []) == {}
