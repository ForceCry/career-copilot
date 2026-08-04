"""Fetch vacancies from one source and upsert them into the DB.

Each source is its own command, on purpose - they have very different
cost/speed profiles (Adzuna: one fast API call. Arbeitnow: rate-limited,
needs delay. justjoin.it: tens of seconds and tens of MB per run), so
coupling them to one schedule would force the cheap ones to wait on the
expensive one.

Run:
  .venv/bin/python scripts/ingest.py --source adzuna
  .venv/bin/python scripts/ingest.py --source arbeitnow
  .venv/bin/python scripts/ingest.py --source justjoinit --keywords php,symfony

Cron example (adjust paths):
  */30 * * * *  cd /path/to/career-copilot && .venv/bin/python scripts/ingest.py --source adzuna
  0 * * * *     cd /path/to/career-copilot && .venv/bin/python scripts/ingest.py --source arbeitnow
  0 6 * * *     cd /path/to/career-copilot && .venv/bin/python scripts/ingest.py --source justjoinit
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlmodel import Session  # noqa: E402

from src.ingestion.sources.adzuna import AdzunaSource  # noqa: E402
from src.ingestion.sources.arbeitnow import ArbeitnowSource  # noqa: E402
from src.ingestion.sources.justjoinit import JustJoinItSource  # noqa: E402
from src.messaging.rabbitmq import publish_vacancy_ids  # noqa: E402
from src.storage.db import engine, init_db  # noqa: E402
from src.storage.vacancy_repo import upsert_vacancies  # noqa: E402

SOURCES = {
    "adzuna": AdzunaSource,
    "arbeitnow": ArbeitnowSource,
    "justjoinit": JustJoinItSource,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, choices=sorted(SOURCES))
    parser.add_argument("--keywords", default="php,symfony,backend")
    parser.add_argument("--location", default="Warsaw")
    args = parser.parse_args()

    init_db()
    source = SOURCES[args.source]()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    print(f"[{args.source}] fetching (keywords={keywords}, location={args.location})...")
    vacancies = source.fetch(keywords, args.location)
    print(f"[{args.source}] fetched {len(vacancies)} vacancies")

    with Session(engine) as session:
        new_count, updated_count, to_embed_ids = upsert_vacancies(session, vacancies)
    print(f"[{args.source}] upserted: {new_count} new, {updated_count} refreshed")

    try:
        publish_vacancy_ids(to_embed_ids)
        print(f"[{args.source}] queued {len(to_embed_ids)} vacancies for embedding")
    except Exception as exc:
        # The MySQL upsert above already committed - these vacancies are
        # now "existing" for every future ingest run, so they'll never
        # get queued again on their own if this fails silently. Caught by
        # an independent Codex review: don't let that happen quietly.
        print(f"[{args.source}] FAILED to queue {len(to_embed_ids)} vacancies for embedding: {exc!r}")
        print(f"[{args.source}] run scripts/backfill_embeddings.py to recover - it queues everything, not just new ids")
        raise


if __name__ == "__main__":
    main()
