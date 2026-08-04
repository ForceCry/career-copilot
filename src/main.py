import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

load_dotenv()  # docker-compose injects env vars directly via env_file; local
# runs (uvicorn, scripts) need this to pick up .env themselves.

from .ingestion.pipeline import fetch_all  # noqa: E402
from .ingestion.sources.adzuna import AdzunaSource  # noqa: E402
from .ingestion.sources.arbeitnow import ArbeitnowSource  # noqa: E402
from .ingestion.sources.justjoinit import JustJoinItSource  # noqa: E402
from .matching.engine import score_vacancies  # noqa: E402
from .matching.llm_scorer import llm_rerank  # noqa: E402
from .storage.db import get_session, init_db  # noqa: E402
from .storage.models import Profile  # noqa: E402

app = FastAPI(title="career-copilot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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


def _compute_recommendations(
    profile: Profile,
    keywords: str,
    location: str,
    include_slow_sources: bool,
    min_score: int,
    llm_rerank_top_n: int,
) -> list:
    profile_skills = [s.name for s in profile.skills]
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    fetched = fetch_all(_configured_sources(include_slow_sources), keyword_list, location)
    heuristic_matches = [m for m in score_vacancies(fetched, profile_skills) if m.score >= min_score]

    if llm_rerank_top_n > 0:
        # LLM calls shell out to the local `claude` CLI - several seconds
        # and real cost per call - so only the top of the cheap heuristic
        # ranking gets reranked, never the full result set.
        by_url = {v.url: v for v in fetched}
        shortlist = [by_url[m.vacancy_url] for m in heuristic_matches[:llm_rerank_top_n]]
        return llm_rerank(shortlist, profile)

    return heuristic_matches


@app.get("/recommendations")
def recommendations(
    keywords: str = "php,symfony,backend",
    location: str = "Warsaw",
    include_slow_sources: bool = False,
    min_score: int = 0,
    llm_rerank_top_n: int = 0,
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")

    matches = _compute_recommendations(
        profile, keywords, location, include_slow_sources, min_score, llm_rerank_top_n
    )
    return {"count": len(matches), "recommendations": [m.model_dump() for m in matches]}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    keywords: str = "php,symfony,backend",
    location: str = "Warsaw",
    min_score: int = 0,
    llm_rerank_top_n: int = 0,
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile)).first()
    recommendations_ctx = []
    error = None

    if not profile:
        error = "No profile seeded yet — run scripts/seed_profile.py"
    else:
        matches = _compute_recommendations(
            profile, keywords, location, False, min_score, llm_rerank_top_n
        )
        for m in matches:
            item = {
                "title": m.vacancy_title,
                "company": m.vacancy_company,
                "url": m.vacancy_url,
                "score": m.score,
            }
            if hasattr(m, "reasoning"):
                item["reasoning"] = m.reasoning
                item["concerns"] = m.concerns
            else:
                item["matched_skills"] = m.matched_skills
            recommendations_ctx.append(item)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "profile": profile,
            "recommendations": recommendations_ctx,
            "keywords": keywords,
            "location": location,
            "min_score": min_score,
            "llm_rerank_top_n": llm_rerank_top_n,
            "error": error,
        },
    )


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
