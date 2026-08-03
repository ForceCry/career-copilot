import os

from fastapi import FastAPI

from .ingestion.pipeline import fetch_all
from .ingestion.sources.adzuna import AdzunaSource
from .ingestion.sources.arbeitnow import ArbeitnowSource

app = FastAPI(title="career-copilot")


def _configured_sources() -> list:
    sources = [ArbeitnowSource(max_pages=2)]
    if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
        sources.append(AdzunaSource())
    return sources


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vacancies")
def vacancies(keywords: str = "php,symfony,backend", location: str = "Warsaw"):
    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    results = fetch_all(_configured_sources(), keyword_list, location)
    return {"count": len(results), "vacancies": [v.model_dump() for v in results]}
