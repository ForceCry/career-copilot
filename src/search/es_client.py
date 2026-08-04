import os

from elasticsearch import BadRequestError, Elasticsearch

VACANCY_INDEX = "vacancies"
EMBEDDING_DIMS = 768  # multilingual-e5-base

_MAPPING = {
    "properties": {
        "vacancy_id": {"type": "integer"},
        "source": {"type": "keyword"},
        "title": {"type": "text"},
        "company": {"type": "keyword"},
        "url": {"type": "keyword"},
        "embedding": {
            "type": "dense_vector",
            "dims": EMBEDDING_DIMS,
            "index": True,
            "similarity": "cosine",
        },
    }
}


def get_client() -> Elasticsearch:
    host = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
    return Elasticsearch(host)


def ensure_index(client: Elasticsearch) -> None:
    # check-then-create has a real race when multiple embedding-worker
    # replicas start against a fresh index at once (we've actually run 4
    # in parallel, for the backfill) - both can see "doesn't exist" and
    # both attempt to create it, and the loser gets a 400
    # resource_already_exists_exception. That's a benign outcome here
    # (the index exists either way), so it's swallowed rather than
    # treated as a real failure.
    if client.indices.exists(index=VACANCY_INDEX):
        return
    try:
        client.indices.create(index=VACANCY_INDEX, mappings=_MAPPING)
    except BadRequestError as e:
        if e.error != "resource_already_exists_exception":
            raise
