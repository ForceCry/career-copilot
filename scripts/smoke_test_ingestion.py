"""Manual check that the ingestion pipeline actually returns real data.
Run: .venv/bin/python scripts/smoke_test_ingestion.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ingestion.pipeline import fetch_all
from ingestion.sources.arbeitnow import ArbeitnowSource

KEYWORDS = ["php", "symfony", "backend"]
LOCATION = "Warsaw"

if __name__ == "__main__":
    sources = [ArbeitnowSource(max_pages=2)]
    vacancies = fetch_all(sources, KEYWORDS, LOCATION)

    print(f"Fetched {len(vacancies)} vacancies from {[s.name for s in sources]}\n")
    for v in vacancies[:10]:
        print(f"- [{v.source}] {v.title} @ {v.company} ({v.location}, remote={v.remote})")
