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
    get_remote_feedback,
    get_seniority_feedback,
    get_skill_feedback,
    list_applications,
    normalize_company_name,
    primary_seniority_level,
    set_status,
    skill_mentioned,
)
from .storage.artifact_repo import list_artifacts_for_vacancies, save_artifact  # noqa: E402
from .storage.db import engine, get_session, init_db  # noqa: E402
from .storage.ingestion_run_repo import list_runs  # noqa: E402
from .storage.models import (  # noqa: E402
    APPLICATION_STATUSES,
    NEGATIVE_APPLICATION_STATUSES,
    Application,
    GeneratedArtifact,
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

# Caps the COMBINED skill+remote+seniority score adjustment before the
# separate 0-100 display clamp - see _feedback_score_adjustment's comment.
# Flagged by an independent Codex review: left unbounded, several stacked
# feedback signals (skill deltas alone can multiply) could swing a score
# far enough to collapse distinct candidates to the same 0/100 floor/
# ceiling, changing min_score eligibility and LLM-shortlist selection more
# than a "soft nudge" should.
FEEDBACK_MAX_TOTAL_ADJUSTMENT = 20

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

# The ingestion adapters registered in src/ingestion/sources/ - see each
# source's `name` class attribute. Listed here (rather than derived from
# the DB at request time) purely to populate the sources filter dropdown;
# adding another adapter means adding it here too.
AVAILABLE_SOURCES = ("adzuna", "arbeitnow", "justjoinit", "dou")


def _normalize_sources(raw: list[str]) -> list[str]:
    """Filters a query-param `sources` list down to known adapter names -
    flagged by an independent Codex review: passed through unvalidated, a
    typo or a stale bookmark from before `sources` switched from a comma-
    separated string to repeated params (e.g. `?sources=adzuna,arbeitnow`,
    now a single unknown value) silently applied as an active filter,
    usually producing an inexplicable empty result set that looks like
    "no matches" rather than "bad filter value". Also splits any
    comma-containing entry, so old bookmarks in that format keep working.
    Unknown values are dropped silently rather than erroring - this is a
    dropdown-driven filter, not a validated API contract."""
    split = [part.strip() for item in raw for part in item.split(",")]
    seen: list[str] = []
    for source in split:
        if source in AVAILABLE_SOURCES and source not in seen:
            seen.append(source)
    return seen


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


@app.get("/ingestion-runs")
def ingestion_runs(
    source: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """History of scripts/ingest.py invocations - whether triggered by
    the opt-in in-stack `scheduler` service, your own external cron, or
    run by hand. `finished_at`/`error` still null means the run either
    hasn't finished yet or crashed without a chance to record why (a
    killed process, an unhandled signal) - either way, worth a look if
    it's been a while."""
    runs = list_runs(session, source=source, limit=limit)
    return {
        "count": len(runs),
        "runs": [
            {
                "id": r.id,
                "source": r.source,
                "keywords": r.keywords,
                "location": r.location,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "fetched_count": r.fetched_count,
                "new_count": r.new_count,
                "updated_count": r.updated_count,
                "error": r.error,
            }
            for r in runs
        ],
    }


def _compute_recommendations(
    session: Session,
    profile: Profile,
    sources: list[str],
    min_score: int,
    top_k: int,
    llm_rerank_top_n: int,
) -> tuple[list, dict[int, Vacancy], dict[int, Application]]:
    hits = vector_search(profile, k=top_k, sources=sources or None)

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
    # Explicit feedback signals from application_repo - a posting's own
    # skill mentions / remote flag / title seniority that the user has
    # consistently dismissed/rejected (disproportionately more than their
    # overall dismiss rate) nudges similar future postings down; one
    # they've consistently engaged with nudges them up. Soft score
    # adjustments, not a hard filter like the company exclusion above -
    # these signals are noisier (a skill mentioned in a description isn't
    # necessarily WHY a posting was dismissed).
    skill_feedback = get_skill_feedback(session, [s.name for s in profile.skills])
    remote_feedback = get_remote_feedback(session)
    seniority_feedback = get_seniority_feedback(session)

    def _not_hidden(vacancy_id: int) -> bool:
        application = applications_by_id.get(vacancy_id)
        return application is None or application.status not in RECOMMENDATION_HIDDEN_STATUSES

    def _company_not_excluded(vacancy_id: int) -> bool:
        vacancy = vacancies_by_id.get(vacancy_id)
        return vacancy is None or normalize_company_name(vacancy.company) not in excluded_companies

    def _feedback_score_adjustment(vacancy_id: int) -> int:
        vacancy = vacancies_by_id.get(vacancy_id)
        if vacancy is None:
            return 0
        adjustment = 0
        if skill_feedback:
            haystack = f"{vacancy.title} {vacancy.description} {' '.join(vacancy.tags)}".lower()
            adjustment += sum(
                delta for skill, delta in skill_feedback.items() if skill_mentioned(skill, haystack)
            )
        if remote_feedback and vacancy.remote:
            # Only "remote" is ever a key here - see get_remote_feedback's
            # docstring for why remote=False can't be trusted as "onsite".
            adjustment += remote_feedback.get("remote", 0)
        if seniority_feedback:
            level = primary_seniority_level(vacancy.title.lower())
            if level:
                adjustment += seniority_feedback.get(level, 0)
        # Independent Codex review: three feedback dimensions stacking
        # unbounded (skill alone can contribute multiple deltas) meant the
        # 0-100 clamp below wasn't really bounding anything - it just
        # collapsed heavily-flagged candidates to 0 or 100, indistinguishable
        # from each other and from the vector score that put them there,
        # before min_score filtering or LLM shortlist selection ever see
        # them. Capping the COMBINED adjustment first keeps these as
        # intended - soft nudges that can reorder results, not swings large
        # enough to override the underlying semantic ranking outright.
        return max(-FEEDBACK_MAX_TOTAL_ADJUSTMENT, min(FEEDBACK_MAX_TOTAL_ADJUSTMENT, adjustment))

    vector_matches = [
        VectorMatchResult(
            vacancy_id=h["vacancy_id"],
            vacancy_title=h["title"],
            vacancy_company=h["company"],
            vacancy_url=h["url"],
            # Clamped to the same 0-100 display scale the raw vector score
            # already uses - the feedback adjustment nudges within that
            # scale, it doesn't get to push a match above 100 or below 0.
            score=max(0, min(100, round(h["score"] * 100) + _feedback_score_adjustment(h["vacancy_id"]))),
        )
        for h in hits
        if h["vacancy_id"] in vacancies_by_id  # guards against a stale ES doc whose MySQL row is gone
        and h["vacancy_id"] in fresh_ids  # excludes postings the source has stopped returning
        and _not_hidden(h["vacancy_id"])  # excludes vacancies explicitly dismissed/rejected
        and _company_not_excluded(h["vacancy_id"])  # excludes companies written off entirely
    ]
    # Feedback adjustments can reorder matches relative to vector_search's
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
    sources: list[str] = Query(default=[]),
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
        session, profile, _normalize_sources(sources), min_score, top_k, llm_rerank_top_n
    )
    return {"count": len(matches), "recommendations": [m.model_dump() for m in matches]}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    sources: list[str] = Query(default=[]),
    min_score: int = Query(0, ge=0, le=100),
    top_k: int = Query(20, ge=1, le=100),
    llm_rerank_top_n: int = Query(0, ge=0, le=10),
    session: Session = Depends(get_session),
):
    profile = session.exec(select(Profile).order_by(Profile.id)).first()
    recommendations_ctx = []
    error = None
    sources = _normalize_sources(sources)

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
            "available_sources": AVAILABLE_SOURCES,
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


@app.post("/vacancies/{vacancy_id}/cover-letter")
def cover_letter(request: Request, vacancy_id: int, session: Session = Depends(get_session)):
    """Generates a cover letter for an already-ingested vacancy and
    persists it (GeneratedArtifact, append-only - see
    artifact_repo.save_artifact), then redirects to GET /artifacts/{id}
    (Post/Redirect/Get) rather than rendering the result directly.
    Flagged by an independent Codex review: returning the rendered page
    straight from the POST meant a page refresh or a browser's "resend
    form data?" resubmission prompt re-ran the LLM call and appended a
    second artifact - real cost and time, not just a display glitch. PRG
    also gives every generated artifact a durable, bookmarkable URL
    immediately, which matters for vacancies not (yet) tracked in
    /applications - that page is the only other place a saved artifact's
    link shows up.

    POST by vacancy_id, not GET with the full posting title/company/url/
    description in the query string (the original design) - this calls
    the local LLM (real cost, several seconds) and now writes to the
    database too, neither of which belongs on a GET, and the vacancy is
    already stored so there's no need to round-trip its text through the
    client at all."""
    if not _same_origin(request, require_header=True):
        raise HTTPException(403, "Cross-origin form submission rejected")
    profile = _get_profile_or_404(session)
    vacancy = get_vacancies_by_ids(session, [vacancy_id]).get(vacancy_id)
    if vacancy is None:
        raise HTTPException(404, "Vacancy not found")

    letter = generate_cover_letter(vacancy, profile)
    artifact = save_artifact(
        session, vacancy_id, "cover_letter", letter, vacancy.title, vacancy.company, vacancy.url
    )
    return RedirectResponse(url=f"/artifacts/{artifact.id}", status_code=303)


@app.post("/vacancies/{vacancy_id}/tailoring-suggestions")
def tailoring_suggestions(request: Request, vacancy_id: int, session: Session = Depends(get_session)):
    """Same as cover_letter above, for resume-tailoring suggestions."""
    if not _same_origin(request, require_header=True):
        raise HTTPException(403, "Cross-origin form submission rejected")
    profile = _get_profile_or_404(session)
    vacancy = get_vacancies_by_ids(session, [vacancy_id]).get(vacancy_id)
    if vacancy is None:
        raise HTTPException(404, "Vacancy not found")

    suggestions = suggest_resume_tailoring(vacancy, profile)
    artifact = save_artifact(
        session, vacancy_id, "tailoring_suggestions", suggestions, vacancy.title, vacancy.company, vacancy.url
    )
    return RedirectResponse(url=f"/artifacts/{artifact.id}", status_code=303)


@app.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def get_artifact(artifact_id: int, request: Request, session: Session = Depends(get_session)):
    """Views a previously-generated, persisted cover letter or tailoring-
    suggestions artifact - re-renders the saved text, no fresh LLM call.
    Renders from the artifact's OWN vacancy_title/company/url snapshot,
    not a live lookup against the current VacancyRecord - flagged by an
    independent Codex review: re-ingestion overwrites those fields in
    place on the vacancy row (title corrections, a changed posting URL),
    so a letter viewed weeks later against the CURRENT record could
    silently appear to be about a different company or link than what it
    was actually written for. The snapshot is immune to that, and to the
    vacancy row being deleted entirely (nothing in this codebase does
    that today, but the artifact doesn't depend on it either way)."""
    artifact = session.get(GeneratedArtifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    page_title = (
        "Cover letter" if artifact.artifact_type == "cover_letter" else "Resume tailoring suggestions"
    )
    return templates.TemplateResponse(
        request,
        "text_result.html",
        {
            "page_title": page_title,
            "vacancy_title": artifact.vacancy_title,
            "vacancy_company": artifact.vacancy_company,
            "vacancy_url": artifact.vacancy_url,
            "body_text": artifact.content,
        },
    )


def _same_origin(request: Request, *, require_header: bool = False) -> bool:
    """Lightweight CSRF mitigation for state-changing POSTs - the API
    being loopback-only doesn't stop an arbitrary page open in the user's
    own browser from POSTing here with a plain HTML form, since the
    browser (not an attacker's server) makes the request. Browsers attach
    Origin - or, failing that, Referer - to cross-origin form submissions,
    so reject a request whose Origin/Referer names a different host:port
    than this one.

    require_header controls what happens when NEITHER header is present.
    False (the default, used by the status-update endpoint) lets it
    through, since some legitimate same-origin requests - curl, tests,
    privacy-hardened browsers - omit both, and a wrong status update is
    cheap to undo. True (used by the cover-letter/tailoring-suggestions
    endpoints below) rejects it instead: those endpoints trigger a real,
    paid LLM subprocess call and a DB write per POST, so failing open on
    missing headers is a materially worse trade there. Flagged by an
    independent Codex review of the original always-lenient version."""
    expected = request.url.netloc
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlparse(value).netloc == expected
    return not require_header
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
    artifacts_by_vacancy = list_artifacts_for_vacancies(session, [a.vacancy_id for a in applications])

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
                "artifacts": artifacts_by_vacancy.get(a.vacancy_id, []),
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
