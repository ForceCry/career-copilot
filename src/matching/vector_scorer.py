from pydantic import BaseModel

from ..search.embeddings import embed
from ..search.es_client import VACANCY_INDEX, get_client
from ..storage.models import Profile


class VectorMatchResult(BaseModel):
    """ES cosine similarity (roughly 0-1) scaled to 0-100 for display
    consistency with the rest of the UI - a different thing being measured
    than the old keyword-overlap score, not a like-for-like number, but
    the same visual scale."""

    vacancy_title: str
    vacancy_company: str
    vacancy_url: str
    score: int


def build_profile_query_text(profile: Profile) -> str:
    skills = ", ".join(s.name for s in profile.skills)
    experience_lines = "\n".join(
        f"{e.title} at {e.company}: {e.highlights}" for e in profile.experiences
    )
    return f"query: {profile.summary}\nSkills: {skills}\n{experience_lines}"


def vector_search(profile: Profile, k: int = 10, sources: list[str] | None = None) -> list[dict]:
    """Returns raw ES hits (vacancy_id, source, title, company, url, score)
    - caller joins back to MySQL for anything else (description, salary,
    etc.), ES only stores what's needed to rank and label a result."""
    vector = embed(build_profile_query_text(profile))

    knn: dict = {
        "field": "embedding",
        "query_vector": vector,
        "k": k,
        "num_candidates": max(50, k * 5),
    }
    if sources:
        knn["filter"] = {"terms": {"source": sources}}

    client = get_client()
    # `size` defaults to 10 on the standard _search endpoint regardless of
    # k inside `knn` - without it, a k=40 request silently comes back
    # capped at 10 results. Confirmed live, not a hypothetical gotcha.
    result = client.search(index=VACANCY_INDEX, knn=knn, size=k)

    return [
        {
            "vacancy_id": hit["_source"]["vacancy_id"],
            "source": hit["_source"]["source"],
            "title": hit["_source"]["title"],
            "company": hit["_source"]["company"],
            "url": hit["_source"]["url"],
            "score": hit["_score"],
        }
        for hit in result["hits"]["hits"]
    ]
