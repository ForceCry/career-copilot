# Diagrams

- `architecture-overview.drawio` — ingestion (3 sources → adapters → MySQL),
  async embedding pipeline (RabbitMQ → embedding-worker → TEI →
  Elasticsearch), and semantic matching (profile → query embedding → kNN
  search → optional LLM rerank → recommendations). For article 1.
- `codex-review-loop.drawio` — the Claude-builds → Codex-reviews →
  human-verifies cycle, drawn as an actual loop (both "fixed" and
  "rejected" feed back into the next round) rather than a single linear
  pass — in practice this runs at least 2 rounds before anything ships.
  For article 2.

Open either at [app.diagrams.net](https://app.diagrams.net) (File → Open
From → Device) or the draw.io desktop app. Export as PNG/SVG from there
for embedding in an actual article — LinkedIn doesn't take `.drawio`
files directly.
