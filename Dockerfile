FROM python:3.12-slim

WORKDIR /app

# Node.js + the claude CLI: the LLM rerank layer shells out to `claude -p`
# using the OAuth session mounted in at runtime (see docker-compose.yml),
# not an API key.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

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

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
