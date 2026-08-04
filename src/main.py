import os

from fastapi import FastAPI

from .ingestion.pipeline import fetch_all
from .ingestion.sources.adzuna import AdzunaSource
from .ingestion.sources.arbeitnow import ArbeitnowSource
from .ingestion.sources.justjoinit import JustJoinItSource

app = FastAPI(title="career-copilot")


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
