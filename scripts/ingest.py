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

Or skip your own host cron entirely: `docker compose --profile scheduler up
-d` runs this same cadence from inside the stack itself, via the opt-in
`scheduler` service (see scheduler/crontab and docs/DEPLOYMENT.md). Either
way, every run - host cron or in-stack - is recorded to the `ingestion_run`
table (GET /ingestion-runs), so a source that's silently stopped returning
results or started erroring is visible without digging through logs.
"""
import argparse
import logging
import sys
import time
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
from src.observability import configure_logging  # noqa: E402
from src.storage.db import engine, init_db  # noqa: E402
from src.storage.ingestion_run_repo import finish_run, start_run  # noqa: E402
from src.storage.vacancy_repo import mark_embedding_queued, upsert_vacancies  # noqa: E402

logger = logging.getLogger("ingest")

SOURCES = {
    "adzuna": AdzunaSource,
    "arbeitnow": ArbeitnowSource,
    "justjoinit": JustJoinItSource,
}


def _record_run_finish(source: str, run_id: int, **kwargs) -> None:
    """Best-effort - flagged by an independent Codex review: finish_run()
    was previously called directly inside main()'s except/else blocks, so
    if IT raised (e.g. MySQL briefly unreachable right as the run
    finishes), that new exception would replace the one actually being
    handled - masking the real ingest failure behind an unrelated DB
    error - or, on the success path, turn a completed ingestion into a
    reported script failure for a reason that has nothing to do with
    ingestion actually working. Recording run history should never be
    able to override the outcome it's just trying to record."""
    try:
        with Session(engine) as session:
            finish_run(session, run_id, **kwargs)
    except Exception:
        logger.exception(
            "failed to record ingestion_run outcome (ingest itself already finished)",
            extra={"source": source, "run_id": run_id},
        )


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", required=True, choices=sorted(SOURCES))
    parser.add_argument("--keywords", default="php,symfony,backend")
    parser.add_argument("--location", default="Warsaw")
    args = parser.parse_args()

    run_start = time.monotonic()
    init_db()
    source = SOURCES[args.source]()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    # IngestionRun is written in two phases (see its docstring) so a
    # crashed/killed run still leaves a row behind - this start_run call
    # has to happen before the (possibly failing) fetch below, not after
    # it, or a source outage would never get recorded at all.
    with Session(engine) as session:
        run = start_run(session, args.source, args.keywords, args.location)

    try:
        logger.info(
            "fetching vacancies",
            extra={"source": args.source, "keywords": keywords, "location": args.location},
        )
        vacancies = source.fetch(keywords, args.location)
        logger.info("fetched vacancies", extra={"source": args.source, "fetched": len(vacancies)})

        with Session(engine) as session:
            new_count, updated_count, to_embed_ids = upsert_vacancies(session, vacancies)
        logger.info(
            "upserted vacancies",
            extra={"source": args.source, "new": new_count, "updated": updated_count},
        )

        try:
            confirmed_ids = publish_vacancy_ids(to_embed_ids)
        except Exception:
            # A total failure to even talk to RabbitMQ (vs. individual
            # messages not confirming) - nothing got marked queued, so the
            # next ingest run naturally retries everything in to_embed_ids
            # on its own (see vacancy_repo.upsert_vacancies:
            # embedding_queued_at stays NULL for all of them). Still
            # surfaced loudly rather than swallowed, since a persistently
            # broken RabbitMQ needs attention.
            logger.exception(
                "failed to queue vacancies for embedding",
                extra={"source": args.source, "to_embed": len(to_embed_ids)},
            )
            raise

        if confirmed_ids:
            with Session(engine) as session:
                mark_embedding_queued(session, confirmed_ids)

        unconfirmed = len(to_embed_ids) - len(confirmed_ids)
        logger.info(
            "ingest run finished",
            extra={
                "source": args.source,
                "fetched": len(vacancies),
                "new": new_count,
                "updated": updated_count,
                "queued": len(confirmed_ids),
                "unconfirmed": unconfirmed,
                "duration_seconds": round(time.monotonic() - run_start, 2),
            },
        )
    except Exception as exc:
        _record_run_finish(args.source, run.id, error=str(exc)[:2000])
        raise
    else:
        _record_run_finish(
            args.source, run.id,
            fetched_count=len(vacancies), new_count=new_count, updated_count=updated_count,
        )


if __name__ == "__main__":
    main()
