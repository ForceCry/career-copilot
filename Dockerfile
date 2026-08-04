FROM python:3.12-slim

WORKDIR /app

# Node.js + the claude CLI: the LLM rerank layer shells out to `claude -p`
# using the OAuth session mounted in at runtime (see docker-compose.yml),
# not an API key.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
