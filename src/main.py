from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from prometheus_fastapi_instrumentator import Instrumentator
from sqlmodel import Session, select

load_dotenv()  # docker-compose injects env vars directly via env_file; local
# runs (uvicorn, scripts) need this to pick up .env themselves.

from .documents.llm_writer import generate_cover_letter, suggest_resume_tailoring  # noqa: E402
from .documents.resume import render_resume_html  # noqa: E402
from .ingestion.models import Vacancy  # noqa: E402
from .matching.llm_scorer import llm_rerank  # noqa: E402
from .matching.vector_scorer import VectorMatchResult, vector_search  # noqa: E402
from .storage.db import get_session, init_db  # noqa: E402
from .storage.models import Profile, ResumeVersion  # noqa: E402
from .storage.vacancy_repo import get_vacancies_by_ids, query_vacancies  # noqa: E402

app = FastAPI(title="career-copilot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

Instrumentator().instrument(app).expose(app)  # request rate/latency/status at GET /metrics


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vacancies")
def vacancies(
    keywords: str = "php,symfony,backend",
    sources: str = "",
    session: Session = Depends(get_session),
):
    """Reads from the DB - populated by `scripts/ingest.py --source ...`,
    not fetched live. Run that first if this comes back empty."""
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    source_list = [s.strip() for s in sources.split(",") if s.strip()] or None
    results = query_vacancies(session, keyword_list, source_list)
    return {"count": len(results), "vacancies": [v.model_dump() for v in results]}


def _compute_recommendations(
    session: Session,
    profile: Profile,
    sources: str,
    min_score: int,
    top_k: int,
    llm_rerank_top_n: int,
) -> tuple[list, dict[str, Vacancy]]:
    source_list = [s.strip() for s in sources.split(",") if s.strip()] or None
    hits = vector_search(profile, k=top_k, sources=source_list)

    vacancies_by_id = get_vacancies_by_ids(session, [h["vacancy_id"] for h in hits])
    by_url = {v.url: v for v in vacancies_by_id.values()}

    vector_matches = [
        VectorMatchResult(
            vacancy_title=h["title"],
            vacancy_company=h["company"],
            vacancy_url=h["url"],
            score=round(h["score"] * 100),
        )
        for h in hits
        if h["vacancy_id"] in vacancies_by_id  # guards against a stale ES doc whose MySQL row is gone
    ]
    vector_matches = [m for m in vector_matches if m.score >= min_score]

    if llm_rerank_top_n > 0:
        # LLM calls shell out to the local `claude` CLI - several seconds
        # and real cost per call - so only the top of the cheap vector
        # search shortlist gets reranked, never the full result set.
        shortlist = [by_url[m.vacancy_url] for m in vector_matches[:llm_rerank_top_n]]
        return llm_rerank(shortlist, profile), by_url

    return vector_matches, by_url


@app.get("/recommendations")
def recommendations(
    sources: str = "",
    min_score: int = 0,
    top_k: int = 20,
    llm_rerank_top_n: int = 0,
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")

    matches, _ = _compute_recommendations(
        session, profile, sources, min_score, top_k, llm_rerank_top_n
    )
    return {"count": len(matches), "recommendations": [m.model_dump() for m in matches]}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    sources: str = "",
    min_score: int = 0,
    top_k: int = 20,
    llm_rerank_top_n: int = 0,
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile)).first()
    recommendations_ctx = []
    error = None

    if not profile:
        error = "No profile seeded yet — run scripts/seed_profile.py"
    else:
        matches, by_url = _compute_recommendations(
            session, profile, sources, min_score, top_k, llm_rerank_top_n
        )
        for m in matches:
            vacancy = by_url.get(m.vacancy_url)
            item = {
                "title": m.vacancy_title,
                "company": m.vacancy_company,
                "url": m.vacancy_url,
                "score": m.score,
                "description": vacancy.description if vacancy else "",
                "salary_min": vacancy.salary_min if vacancy else None,
                "salary_max": vacancy.salary_max if vacancy else None,
                "salary_currency": vacancy.salary_currency if vacancy else "",
                "salary_period": vacancy.salary_period if vacancy else "",
                "salary_is_predicted": vacancy.salary_is_predicted if vacancy else False,
            }
            if hasattr(m, "reasoning"):
                item["reasoning"] = m.reasoning
                item["concerns"] = m.concerns
            elif hasattr(m, "matched_skills"):
                item["matched_skills"] = m.matched_skills
            recommendations_ctx.append(item)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "profile": profile,
            "recommendations": recommendations_ctx,
            "sources": sources,
            "min_score": min_score,
            "top_k": top_k,
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


def _get_profile_or_404(session: Session) -> Profile:
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")
    return profile


@app.get("/resume", response_class=HTMLResponse)
def resume(session: Session = Depends(get_session)):
    """Always renders live from the current profile data - not a stored
    snapshot. Use POST /resume/versions to save a snapshot worth keeping."""
    return HTMLResponse(render_resume_html(_get_profile_or_404(session)))


@app.post("/resume/versions")
def save_resume_version(label: str = "", session: Session = Depends(get_session)):
    profile = _get_profile_or_404(session)
    version = ResumeVersion(
        profile_id=profile.id, label=label, content_html=render_resume_html(profile)
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return {"id": version.id, "label": version.label, "created_at": version.created_at}


@app.get("/resume/versions")
def list_resume_versions(session: Session = Depends(get_session)):
    profile = _get_profile_or_404(session)
    return [
        {"id": v.id, "label": v.label, "created_at": v.created_at}
        for v in sorted(profile.resume_versions, key=lambda v: v.created_at, reverse=True)
    ]


@app.get("/resume/versions/{version_id}", response_class=HTMLResponse)
def get_resume_version(version_id: int, session: Session = Depends(get_session)):
    version = session.get(ResumeVersion, version_id)
    if not version:
        raise HTTPException(404, "Resume version not found")
    return HTMLResponse(version.content_html)


def _vacancy_from_params(title: str, company: str, url: str, description: str) -> Vacancy:
    # Takes vacancy details straight from the link/form that triggered the
    # action rather than looking up a stored record by id - simpler, and
    # these routes don't otherwise need a DB round trip.
    return Vacancy(
        source="manual", external_id=url, title=title, company=company,
        location="", remote=False, url=url, description=description, tags=[],
    )


@app.get("/cover-letter", response_class=HTMLResponse)
def cover_letter(
    request: Request,
    title: str,
    company: str,
    url: str,
    description: str,
    session: Session = Depends(get_session),
):
    profile = _get_profile_or_404(session)
    vacancy = _vacancy_from_params(title, company, url, description)
    letter = generate_cover_letter(vacancy, profile)
    return templates.TemplateResponse(
        request,
        "text_result.html",
        {
            "page_title": "Cover letter",
            "vacancy_title": title,
            "vacancy_company": company,
            "vacancy_url": url,
            "body_text": letter,
        },
    )


@app.get("/tailoring-suggestions", response_class=HTMLResponse)
def tailoring_suggestions(
    request: Request,
    title: str,
    company: str,
    url: str,
    description: str,
    session: Session = Depends(get_session),
):
    profile = _get_profile_or_404(session)
    vacancy = _vacancy_from_params(title, company, url, description)
    suggestions = suggest_resume_tailoring(vacancy, profile)
    return templates.TemplateResponse(
        request,
        "text_result.html",
        {
            "page_title": "Resume tailoring suggestions",
            "vacancy_title": title,
            "vacancy_company": company,
            "vacancy_url": url,
            "body_text": suggestions,
        },
    )
