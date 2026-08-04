from sqlmodel import Session

from ..storage.models import VacancyRecord
from .embeddings import embed
from .es_client import VACANCY_INDEX, ensure_index, get_client


def index_vacancy(session: Session, vacancy_id: int) -> bool:
    """Embeds and indexes one vacancy. Returns False if the id doesn't
    exist (e.g. deleted between publish and consume) rather than raising -
    the worker logs and moves on, one missing vacancy shouldn't halt the
    queue."""
    record = session.get(VacancyRecord, vacancy_id)
    if not record:
        return False

    text = f"passage: {record.title}\n\n{record.description}"
    vector = embed(text)

    client = get_client()
    ensure_index(client)
    client.index(
        index=VACANCY_INDEX,
        id=record.id,
        document={
            "vacancy_id": record.id,
            "source": record.source,
            "title": record.title,
            "company": record.company,
            "url": record.url,
            "embedding": vector,
        },
    )
    return True
