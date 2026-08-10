# Diagrams

- `architecture-overview.drawio` — ingestion (3 sources → adapters → MySQL),
  async embedding pipeline (RabbitMQ → embedding-worker → TEI →
  Elasticsearch), and semantic matching (profile → query embedding → kNN
  search → optional LLM rerank → recommendations). Open at
  [app.diagrams.net](https://app.diagrams.net) (File → Open From → Device)
  or the draw.io desktop app. Export as PNG/SVG from there for embedding
  in an actual article — LinkedIn doesn't take `.drawio` files directly.
