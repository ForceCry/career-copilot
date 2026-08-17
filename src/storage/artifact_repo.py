from collections import defaultdict
from datetime import datetime
from typing import NamedTuple

from sqlmodel import Session, select

from .models import ARTIFACT_TYPES, GeneratedArtifact


def save_artifact(
    session: Session,
    vacancy_id: int,
    artifact_type: str,
    content: str,
    vacancy_title: str,
    vacancy_company: str,
    vacancy_url: str,
) -> GeneratedArtifact:
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"Unknown artifact_type {artifact_type!r} - must be one of {ARTIFACT_TYPES}")
    artifact = GeneratedArtifact(
        vacancy_id=vacancy_id,
        artifact_type=artifact_type,
        content=content,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
        vacancy_url=vacancy_url,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def list_artifacts_for_vacancy(session: Session, vacancy_id: int) -> list[GeneratedArtifact]:
    return session.exec(
        select(GeneratedArtifact)
        .where(GeneratedArtifact.vacancy_id == vacancy_id)
        # id, not created_at - MySQL's DATETIME column here has no
        # fractional-second precision (same gotcha already hit with
        # ApplicationEvent), so two artifacts generated in the same
        # second would otherwise sort arbitrarily relative to each
        # other. id is monotonic with insertion order regardless.
        .order_by(GeneratedArtifact.id.desc())
    ).all()


class ArtifactSummary(NamedTuple):
    """Just enough to render a link + label on the /applications listing -
    not the full row. Flagged by an independent Codex review: the
    original list_artifacts_for_vacancies pulled every artifact's full
    (unbounded) content TEXT for a page that only ever renders id/type/
    created_at, growing that page's DB transfer/memory for no visible
    benefit as generations accumulate. Callers that need the actual
    content should use list_artifacts_for_vacancy (singular) or fetch the
    row by id directly - see GET /artifacts/{id}."""

    id: int
    vacancy_id: int
    artifact_type: str
    created_at: datetime


def list_artifacts_for_vacancies(
    session: Session, vacancy_ids: list[int]
) -> dict[int, list[ArtifactSummary]]:
    """Bulk lookup for the /applications page - one query for every
    tracked vacancy's saved artifacts, not one query per row, same
    pattern as application_repo.get_applications_map."""
    if not vacancy_ids:
        return {}
    rows = session.exec(
        select(
            GeneratedArtifact.id,
            GeneratedArtifact.vacancy_id,
            GeneratedArtifact.artifact_type,
            GeneratedArtifact.created_at,
        )
        .where(GeneratedArtifact.vacancy_id.in_(vacancy_ids))
        .order_by(GeneratedArtifact.id.desc())
    ).all()
    by_vacancy: dict[int, list[ArtifactSummary]] = defaultdict(list)
    for row in rows:
        by_vacancy[row.vacancy_id].append(ArtifactSummary(*row))
    return dict(by_vacancy)
