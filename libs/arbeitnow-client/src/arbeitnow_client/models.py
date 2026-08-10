
from pydantic import BaseModel


class Job(BaseModel):
    external_id: str
    title: str
    company: str
    location: str
    remote: bool
    url: str
    description: str
    tags: list[str] = []
    created_at: int | None = None  # unix timestamp, confirmed live - not a string
