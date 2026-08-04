import os

from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import Session, select

from .ingestion.pipeline import fetch_all
from .ingestion.sources.adzuna import AdzunaSource
from .ingestion.sources.arbeitnow import ArbeitnowSource
from .ingestion.sources.justjoinit import JustJoinItSource
from .matching.engine import score_vacancies
from .storage.db import get_session, init_db
from .storage.models import Profile

app = FastAPI(title="career-copilot")


@app.on_event("startup")
def on_startup():
    init_db()


def _configured_sources(include_slow_sources: bool) -> list:
    sources = [ArbeitnowSource(max_pages=2)]
    if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        sources.append(AdzunaSource())
    if include_slow_sources:
        # JustJoinItSource fetches one full job page per match (no bulk
        # search API for this source, see its docstring) - tens of seconds
        # and tens of MB per call. Fine for a scheduled ingestion job,
        # too slow to run on every live request by default.
        sources.append(JustJoinItSource())
    return sources


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vacancies")
def vacancies(
    keywords: str = "php,symfony,backend",
    location: str = "Warsaw",
    include_slow_sources: bool = False,
):
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    results = fetch_all(_configured_sources(include_slow_sources), keyword_list, location)
    return {"count": len(results), "vacancies": [v.model_dump() for v in results]}


@app.get("/recommendations")
def recommendations(
    keywords: str = "php,symfony,backend",
    location: str = "Warsaw",
    include_slow_sources: bool = False,
    min_score: int = 0,
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")
    profile_skills = [s.name for s in profile.skills]

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    fetched = fetch_all(_configured_sources(include_slow_sources), keyword_list, location)
    matches = [m for m in score_vacancies(fetched, profile_skills) if m.score >= min_score]

    return {"count": len(matches), "recommendations": [m.model_dump() for m in matches]}


@app.get("/profile")
def get_profile(session: Session = Depends(get_session)):
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")

    return {
        "full_name": profile.full_name,
        "location": profile.location,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "summary": profile.summary,
        "languages": profile.languages,
        "skills": [{"name": s.name, "category": s.category} for s in profile.skills],
        "experiences": [
            {
                "title": e.title,
                "company": e.company,
                "location": e.location,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "highlights": e.highlights,
            }
            for e in profile.experiences
        ],
        "educations": [
            {
                "institution": ed.institution,
                "degree": ed.degree,
                "field": ed.field,
                "start_date": ed.start_date,
                "end_date": ed.end_date,
            }
            for ed in profile.educations
        ],
    }
