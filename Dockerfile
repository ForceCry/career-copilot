FROM python:3.12-slim

WORKDIR /app

# Node.js + the claude CLI: the LLM rerank layer shells out to `claude -p`
# using the OAuth session mounted in at runtime (see docker-compose.yml),
# not an API key.
# Pinned rather than @latest - an independent Codex review flagged that
# an unpinned global install means every image rebuild can silently pull
# in a newer claude-code CLI with different flags/output-format behavior
# than what this codebase was written against. Bump deliberately.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.221

# Build context is the parent Work/ directory (see docker-compose.yml),
# not career-copilot/ itself - needed to reach these sibling library
# repos, each extracted as its own standalone, independently installable
# package (not published anywhere yet, so installed from source here).
COPY adzuna-client /libs/adzuna-client
COPY arbeitnow-client /libs/arbeitnow-client
COPY justjoinit-scraper /libs/justjoinit-scraper
RUN pip install --no-cache-dir /libs/adzuna-client /libs/arbeitnow-client /libs/justjoinit-scraper

COPY career-copilot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY career-copilot/src ./src
COPY career-copilot/scripts ./scripts
COPY career-copilot/alembic ./alembic
COPY career-copilot/alembic.ini .

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
