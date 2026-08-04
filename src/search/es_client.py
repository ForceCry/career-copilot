import os

from elasticsearch import Elasticsearch

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
    if not client.indices.exists(index=VACANCY_INDEX):
        client.indices.create(index=VACANCY_INDEX, mappings=_MAPPING)
