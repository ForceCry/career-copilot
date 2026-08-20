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
    created_at: int | None = None  # unix timestamp, parsed from the feed's pubDate
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
