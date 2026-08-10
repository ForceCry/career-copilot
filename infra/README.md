# infra

Local infrastructure for the embedding service (Hugging Face
[text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference),
serving `intfloat/multilingual-e5-base`). The service definition itself
lives in the root `docker-compose.yml` (`embeddings` service) - this
directory just holds the model cache.

Used to be its own repo (`career-copilot-infra`) published on a shared
cross-project Docker network, on the theory it might be reused by other
projects later. That never happened, and the indirection made the whole
system harder to understand for no real benefit, so it was merged into
this repo.

`data/hf-cache/` holds the downloaded model weights (~1GB) - gitignored,
not committed. Created automatically on first `docker compose up`, see
the root README's Setup section.
