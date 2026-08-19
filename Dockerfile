FROM python:3.12-slim

# Set by BuildKit automatically (default builder as of Docker 23+) to the
# target platform's arch - explicit ARG is required to actually read it,
# per Docker's own docs. Needed below since python:3.12-slim is itself
# multi-arch (this image already runs on Apple Silicon dev machines via
# emulation or native arm64 builds) but supercronic ships separate
# per-arch binaries with no universal/fat binary option.
ARG TARGETARCH

WORKDIR /app

# Node.js + the claude CLI: the LLM rerank layer shells out to `claude -p`
# using the OAuth session mounted in at runtime (see docker-compose.yml),
# not an API key.
# Pinned rather than @latest - an independent Codex review flagged that
# an unpinned global install means every image rebuild can silently pull
# in a newer claude-code CLI with different flags/output-format behavior
# than what this codebase was written against. Bump deliberately.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm curl \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.221

# supercronic: runs the opt-in `scheduler` service's crontab (see
# docker-compose.yml, scheduler/crontab) - not the system `cron` package.
# Regular cron daemons assume a full init system with syslog and expect
# to run as root and drop privileges per-job; supercronic is a single
# static binary built specifically to run a crontab as a normal
# foreground container process, as whatever user starts it (this image's
# non-root appuser, same as everything else here) and logs to stdout.
# Pinned by version+checksum, not @latest - same reasoning as the claude
# CLI install above. Per-arch checksum picked via TARGETARCH - flagged by
# an independent Codex review: the original version hard-coded
# supercronic-linux-amd64, which would fail with an exec-format error on
# an arm64 build (Apple Silicon, notably) despite the base image itself
# being multi-arch. Both checksums are from supercronic's own v0.2.49
# release notes, verified via two independent fetches of the release
# page for each.
ENV SUPERCRONIC_VERSION=v0.2.49
RUN case "${TARGETARCH}" in \
        amd64) SUPERCRONIC_SHA1SUM=e63c11a9726b775a6a11801e81af4f3fb926aa68 ;; \
        arm64) SUPERCRONIC_SHA1SUM=0b6c5bb743e0b0dafed1132198c81807927ac413 ;; \
        *) echo "unsupported TARGETARCH for supercronic: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${SUPERCRONIC_SHA1SUM}  supercronic-linux-${TARGETARCH}" | sha1sum -c - \
    && chmod +x "supercronic-linux-${TARGETARCH}" \
    && mv "supercronic-linux-${TARGETARCH}" /usr/local/bin/supercronic

# Build context is this repo's own root now (monorepo - libs/ holds the
# three job-board ingestion packages as standalone, independently
# installable/testable packages in their own right, not published
# anywhere yet, so installed from source here).
COPY libs/adzuna-client /libs/adzuna-client
COPY libs/arbeitnow-client /libs/arbeitnow-client
COPY libs/justjoinit-scraper /libs/justjoinit-scraper
RUN pip install --no-cache-dir /libs/adzuna-client /libs/arbeitnow-client /libs/justjoinit-scraper

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY scheduler ./scheduler
COPY alembic ./alembic
COPY alembic.ini .

# Runs as a non-root user rather than the image's default root - flagged
# by an independent Codex review. Confirmed live that neither uvicorn
# (binds :8000, a non-privileged port) nor `claude -p` (only needs to
# read the two files docker-compose.yml mounts read-only into its home
# dir, plus write scratch/cache state elsewhere under that same home
# dir) need root. /app and the npm/pip-installed binaries are
# world-readable by default (COPY/pip/npm), so appuser just needs its
# own writable home directory - created here, not left to bind-mount
# ownership at runtime.
RUN useradd --create-home --home-dir /home/appuser appuser
USER appuser
ENV HOME=/home/appuser

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
