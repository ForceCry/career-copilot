from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text
from sqlmodel import Session, select

load_dotenv()  # docker-compose injects env vars directly via env_file; local
# runs (uvicorn, scripts) need this to pick up .env themselves.

from .documents.llm_writer import generate_cover_letter, suggest_resume_tailoring  # noqa: E402
from .documents.resume import render_resume_html  # noqa: E402
from .ingestion.models import Vacancy  # noqa: E402
from .matching.llm_scorer import llm_rerank  # noqa: E402
from .matching.vector_scorer import VectorMatchResult, vector_search  # noqa: E402
from .observability import configure_logging  # noqa: E402
from .salary import monthly_salary  # noqa: E402
from .search.es_client import get_client as get_es_client  # noqa: E402
from .storage.application_repo import (  # noqa: E402
    get_applications_map,
    get_excluded_companies,
    get_skill_feedback,
    list_applications,
    normalize_company_name,
    set_status,
    skill_mentioned,
)
from .storage.db import engine, get_session, init_db  # noqa: E402
from .storage.models import (  # noqa: E402
    APPLICATION_STATUSES,
    NEGATIVE_APPLICATION_STATUSES,
    Application,
    Profile,
    ResumeVersion,
    VacancyRecord,
)
from .storage.vacancy_repo import get_fresh_vacancy_ids, get_vacancies_by_ids, query_vacancies  # noqa: E402

# Statuses that mean "don't show me this again" - excluded from the main
# recommendations feed by default. Everything else (saved/applied/
# interviewing/offer) stays visible there too, with a status badge - only
# an explicit dismiss/reject should make a vacancy disappear from view,
# not just having been acted on. /applications is the dedicated view for
# seeing every tracked vacancy together regardless of status.
RECOMMENDATION_HIDDEN_STATUSES = NEGATIVE_APPLICATION_STATUSES

configure_logging()

# A vacancy that stops appearing in its source's ingestion results (the
# posting closed, or dropped out of the crawled/searched set) keeps its
# last confirmed last_seen_at forever, since upsert_vacancies only
# refreshes rows the current batch actually saw - nothing ever notices
# and removes it. Confirmed live: a justjoin.it posting that started
# 404ing was still ranking as the #1 recommendation days after it left
# the site's own active-jobs sitemap, because vector search has no
# notion of recency at all. 5 days is generous against each source's
# real cadence (Adzuna every ~30min, Arbeitnow hourly, justjoin.it daily
# - see scripts/ingest.py's docstring) while still excluding something
# that's gone quiet for multiple missed cycles.
RECOMMENDATION_STALE_AFTER = timedelta(days=5)

app = FastAPI(title="career-copilot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

Instrumentator().instrument(app).expose(app)  # request rate/latency/status at GET /metrics


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    # Previously always returned ok regardless of MySQL/ES reachability -
    # an independent Codex review pointed out that lets orchestration
    # route traffic to an instance that can't actually serve anything.
    # TEI/RabbitMQ aren't checked: the app degrades gracefully without
    # them (no vector search / no embedding pipeline progress) rather
    # than failing every request, so they're not "is this API healthy"
    # signals the same way MySQL/ES are.
    problems = []
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
    except Exception as exc:
        problems.append(f"mysql: {exc}")

    try:
        if not get_es_client().ping():
            problems.append("elasticsearch: ping failed")
    except Exception as exc:
        problems.append(f"elasticsearch: {exc}")

    if problems:
        raise HTTPException(503, {"status": "unhealthy", "problems": problems})
    return {"status": "ok"}


@app.get("/vacancies")
def vacancies(
    keywords: str = "php,symfony,backend",
    sources: str = "",
    session: Session = Depends(get_session),
):
    """Reads from the DB - populated by `scripts/ingest.py --source ...`,
    not fetched live. Run that first if this comes back empty.

    Deliberately unpaginated for now (flagged by an independent Codex
    review) - single-user local tool, no auth, and `query_vacancies`
    already keyword-filters server-side before this returns. Revisit
    with an actual limit/offset if the ingested table passes ~5k rows
    or a single response body becomes noticeably slow to render/parse;
    below that, pagination is complexity this endpoint doesn't earn yet.
    """
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
) -> tuple[list, dict[int, Vacancy], dict[int, Application]]:
    source_list = [s.strip() for s in sources.split(",") if s.strip()] or None
    hits = vector_search(profile, k=top_k, sources=source_list)

    hit_ids = [h["vacancy_id"] for h in hits]
    vacancies_by_id = get_vacancies_by_ids(session, hit_ids)
    fresh_ids = get_fresh_vacancy_ids(session, hit_ids, RECOMMENDATION_STALE_AFTER)
    applications_by_id = get_applications_map(session, hit_ids)
    # Explicit feedback signal from application_repo.get_excluded_companies:
    # a company where every tracked application ended up dismissed/rejected
    # is skipped entirely going forward, not just the specific posting(s)
    # already acted on - deterministic, applied before any LLM rerank so
    # the shortlist it sees is already cleaned up.
    excluded_companies = get_excluded_companies(session)
    # Explicit feedback signal from application_repo.get_skill_feedback: a
    # candidate's own profile skill that's mentioned in postings the user
    # has consistently dismissed/rejected nudges similar future postings
    # down; one they've consistently engaged with (saved/applied/...)
    # nudges them up. A soft score adjustment, not a hard filter like the
    # company exclusion above - the signal is noisier (a skill mentioned
    # in a description isn't necessarily WHY a posting was dismissed).
    skill_feedback = get_skill_feedback(session, [s.name for s in profile.skills])

    def _not_hidden(vacancy_id: int) -> bool:
        application = applications_by_id.get(vacancy_id)
        return application is None or application.status not in RECOMMENDATION_HIDDEN_STATUSES

    def _company_not_excluded(vacancy_id: int) -> bool:
        vacancy = vacancies_by_id.get(vacancy_id)
        return vacancy is None or normalize_company_name(vacancy.company) not in excluded_companies

    def _skill_score_adjustment(vacancy_id: int) -> int:
        vacancy = vacancies_by_id.get(vacancy_id)
        if vacancy is None or not skill_feedback:
            return 0
        haystack = f"{vacancy.title} {vacancy.description} {' '.join(vacancy.tags)}".lower()
        return sum(delta for skill, delta in skill_feedback.items() if skill_mentioned(skill, haystack))

    vector_matches = [
        VectorMatchResult(
            vacancy_id=h["vacancy_id"],
            vacancy_title=h["title"],
            vacancy_company=h["company"],
            vacancy_url=h["url"],
            # Clamped to the same 0-100 display scale the raw vector score
            # already uses - the skill adjustment nudges within that scale,
            # it doesn't get to push a match above 100 or below 0.
            score=max(0, min(100, round(h["score"] * 100) + _skill_score_adjustment(h["vacancy_id"]))),
        )
        for h in hits
        if h["vacancy_id"] in vacancies_by_id  # guards against a stale ES doc whose MySQL row is gone
        and h["vacancy_id"] in fresh_ids  # excludes postings the source has stopped returning
        and _not_hidden(h["vacancy_id"])  # excludes vacancies explicitly dismissed/rejected
        and _company_not_excluded(h["vacancy_id"])  # excludes companies written off entirely
    ]
    # Skill adjustments can reorder matches relative to vector_search's
    # original (unadjusted) ranking - re-sort before min_score filtering
    # and before slicing the LLM rerank shortlist, so both see the
    # feedback-adjusted order, not the raw ES one.
    vector_matches.sort(key=lambda m: m.score, reverse=True)
    vector_matches = [m for m in vector_matches if m.score >= min_score]

    if llm_rerank_top_n > 0:
        # LLM calls shell out to the local `claude` CLI - several seconds
        # and real cost per call - so only the top of the cheap vector
        # search shortlist gets reranked, never the full result set. Keyed
        # by id, not url: two postings can share a url (e.g. a re-post),
        # which previously let one silently overwrite the other in the
        # shortlist lookup - flagged by an independent Codex review.
        shortlist = [(m.vacancy_id, vacancies_by_id[m.vacancy_id]) for m in vector_matches[:llm_rerank_top_n]]
        return llm_rerank(shortlist, profile), vacancies_by_id, applications_by_id

    return vector_matches, vacancies_by_id, applications_by_id


@app.get("/recommendations")
def recommendations(
    sources: str = "",
    min_score: int = Query(0, ge=0, le=100),
    # Unbounded top_k/llm_rerank_top_n let a caller force oversized kNN
    # requests or trigger many Claude subprocess calls per request -
    # flagged by an independent Codex review. There's no auth on this
    # API (single-user local tool, see docker-compose.yml's port
    # binding), so this cap is the only thing standing between "normal
    # use" and "someone scripts a loop against it."
    top_k: int = Query(20, ge=1, le=100),
    llm_rerank_top_n: int = Query(0, ge=0, le=10),
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    if not profile:
        raise HTTPException(404, "No profile seeded yet - run scripts/seed_profile.py")

    matches, _, _ = _compute_recommendations(
        session, profile, sources, min_score, top_k, llm_rerank_top_n
    )
    return {"count": len(matches), "recommendations": [m.model_dump() for m in matches]}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    sources: str = "",
    min_score: int = Query(0, ge=0, le=100),
    top_k: int = Query(20, ge=1, le=100),
    llm_rerank_top_n: int = Query(0, ge=0, le=10),
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    recommendations_ctx = []
    error = None

    if not profile:
        error = "No profile seeded yet — run scripts/seed_profile.py"
    else:
        matches, vacancies_by_id, applications_by_id = _compute_recommendations(
            session, profile, sources, min_score, top_k, llm_rerank_top_n
        )
        for m in matches:
            vacancy = vacancies_by_id.get(m.vacancy_id)
            application = applications_by_id.get(m.vacancy_id)
            monthly = (
                monthly_salary(vacancy.salary_min, vacancy.salary_max, vacancy.salary_period)
                if vacancy else None
            )
            item = {
                "vacancy_id": m.vacancy_id,
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
                "salary_monthly_min": monthly[0] if monthly else None,
                "salary_monthly_max": monthly[1] if monthly else None,
                "application_status": application.status if application else None,
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
            "application_statuses": APPLICATION_STATUSES,
        },
    )


@app.get("/profile")
def get_profile(session: Session = Depends(get_session)):
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
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
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
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


def _same_origin(request: Request) -> bool:
    """Lightweight CSRF mitigation for the state-changing POST below - the
    API being loopback-only doesn't stop an arbitrary page open in the
    user's own browser from POSTing here with a plain HTML form, since the
    browser (not an attacker's server) makes the request. Browsers attach
    Origin - or, failing that, Referer - to cross-origin form submissions,
    so reject a request whose Origin/Referer names a different host:port
    than this one. Neither header being present is allowed through rather
    than rejected, since some legitimate same-origin requests (curl,
    tests, privacy-hardened browsers) omit both. Flagged by an independent
    Codex review."""
    expected = request.url.netloc
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlparse(value).netloc == expected
    return True


def _safe_redirect(path: str) -> str:
    """redirect_to below is a client-controlled hidden form field -
    restricting it to same-origin absolute paths prevents this endpoint
    being used as an open redirect (e.g. a malicious page POSTing here
    with redirect_to=https://phishing.example, or a protocol-relative
    //evil.example), flagged by an independent Codex review. Falls back
    to / for anything that doesn't look like a local path rather than
    erroring - a bad redirect_to shouldn't block the status update that
    already succeeded."""
    if path.startswith("/") and not path.startswith("//"):
        return path
    return "/"


@app.post("/vacancies/{vacancy_id}/status")
def update_vacancy_status(
    request: Request,
    vacancy_id: int,
    status: str = Form(...),
    notes: str = Form(""),
    follow_up_at: str = Form(""),
    # An explicit flag, not "notes/follow_up_at present vs absent" -
    # confirmed live that FastAPI/Starlette collapse an explicitly
    # submitted empty form field to None for `str | None = Form(None)`
    # params, the same as a field that was never submitted at all, so
    # that distinction doesn't survive Form parsing and can't be used to
    # tell "clear this" apart from "don't touch this". The quick-select
    # on the recommendations page (only ever submits `status`) leaves
    # this False; the /applications full-edit form always sets it True,
    # even when clearing notes/follow_up_at to empty. Real data-loss bug,
    # flagged by an independent Codex review: the original code had no
    # such flag at all and always overwrote both fields unconditionally.
    update_details: bool = Form(False),
    redirect_to: str = Form("/"),
    session: Session = Depends(get_session),
):
    """Tracks the user's relationship to a vacancy - saved / applied /
    interviewing / offer / rejected / dismissed. Plain HTML form POST +
    redirect, same no-JS style as the rest of this app - called from both
    / and /applications, with redirect_to telling it which page to send
    the user back to afterward."""
    if not _same_origin(request):
        raise HTTPException(403, "Cross-origin form submission rejected")
    if status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Unknown status {status!r} - must be one of {APPLICATION_STATUSES}")
    if not session.get(VacancyRecord, vacancy_id):
        raise HTTPException(404, "Vacancy not found")

    if not update_details:
        set_status(session, vacancy_id, status)
        return RedirectResponse(url=_safe_redirect(redirect_to), status_code=303)

    if follow_up_at:
        try:
            parsed_follow_up = date.fromisoformat(follow_up_at)
        except ValueError:
            # Previously unguarded - flagged by an independent Codex
            # review: a malformed date crashed this into a raw 500
            # instead of a client error.
            raise HTTPException(422, f"follow_up_at must be an ISO date (YYYY-MM-DD), got {follow_up_at!r}")
    else:
        parsed_follow_up = None

    set_status(session, vacancy_id, status, notes=notes, follow_up_at=parsed_follow_up)
    return RedirectResponse(url=_safe_redirect(redirect_to), status_code=303)


@app.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, session: Session = Depends(get_session)):
    """The pipeline view - every tracked vacancy (anything with at least
    one status change ever made), grouped by current status. Untracked
    vacancies never show up here - see / for the semantic-match feed."""
    applications = list_applications(session)
    vacancies_by_id = get_vacancies_by_ids(session, [a.vacancy_id for a in applications])

    by_status: dict[str, list[dict]] = {s: [] for s in APPLICATION_STATUSES}
    for a in applications:
        vacancy = vacancies_by_id.get(a.vacancy_id)
        if not vacancy:
            # the vacancy row itself is gone; don't let one orphaned application break the whole page
            continue
        by_status[a.status].append(
            {
                "vacancy_id": a.vacancy_id,
                "title": vacancy.title,
                "company": vacancy.company,
                "url": vacancy.url,
                "status": a.status,
                "notes": a.notes,
                "follow_up_at": a.follow_up_at,
                "updated_at": a.updated_at,
            }
        )

    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "by_status": by_status,
            "application_statuses": APPLICATION_STATUSES,
        },
    )
