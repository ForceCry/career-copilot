import json
import logging

from prometheus_client import Counter, Histogram

# Standard attributes every LogRecord has - anything else on the record is
# something a caller passed via logging.info(..., extra={...}), and gets
# folded into the JSON output as structured fields.
_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        payload.update(
            {k: v for k, v in record.__dict__.items() if k not in _STANDARD_LOG_RECORD_ATTRS}
        )
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Structured (JSON) logging to stdout, so container logs are
    grep/jq-able instead of free-text print() output - previously
    scripts/embedding_worker.py and scripts/ingest.py printed plain text,
    and llm_scorer.py/llm_writer.py logged nothing at all on failure, so an
    LLM call silently returning None (bad JSON, non-zero exit, is_error) or
    timing out left no trace anywhere. Idempotent - safe to call from every
    entrypoint (main.py, each script) even if already configured."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # httpx logs each request's full URL at INFO - for the Adzuna source
    # that URL includes app_id/app_key as query params (its auth scheme).
    # Confirmed live: turning on root INFO logging made this an actual
    # credential leak into container logs, not just a hypothetical one -
    # adzuna_client.errors.AdzunaAPIError already avoids this for error
    # responses (see that repo's client.py), but httpx's own per-request
    # logging is separate from that and wasn't covered by it. Raised
    # specifically rather than lowering root's level, so genuine
    # httpx-level warnings/errors still surface normally.
    logging.getLogger("httpx").setLevel(logging.WARNING)


# Both llm_scorer.py (score_vacancy) and llm_writer.py (cover_letter,
# tailoring_suggestions) call the local `claude` CLI - shared metrics here
# so /metrics (exposed by the api process, where all of these run) has one
# consistent llm_calls_total/llm_call_seconds pair across every operation,
# not three near-duplicate sets of metrics.
LLM_CALLS = Counter(
    "llm_calls_total",
    "Local `claude` CLI subprocess calls, by operation and outcome",
    ["operation", "outcome"],
)
LLM_CALL_DURATION = Histogram(
    "llm_call_seconds",
    "Wall-clock time for a `claude` CLI subprocess call",
    ["operation"],
    # prometheus_client's default buckets top out at 10s - confirmed live,
    # a real score_vacancy call took ~16.7s and landed entirely in the
    # +Inf bucket, making p95/p99 queries meaningless. Timeouts here are
    # 30s (score_vacancy) and 60s (cover_letter/tailoring_suggestions), so
    # buckets extend well past both.
    buckets=(1, 2.5, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, float("inf")),
)
