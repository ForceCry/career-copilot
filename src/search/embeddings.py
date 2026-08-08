import httpx

from ..settings import settings


def embed(text: str, timeout: float = 30.0) -> list[float]:
    """TEI truncates silently past its 512-token limit (auto_truncate: true,
    confirmed in its own startup log) rather than erroring - long resumes/
    descriptions lose their tail, not the whole request."""
    response = httpx.post(settings.embeddings_url, json={"inputs": text}, timeout=timeout)
    response.raise_for_status()
    return response.json()[0]
