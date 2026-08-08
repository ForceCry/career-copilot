from datetime import datetime

from pydantic import BaseModel


class Vacancy(BaseModel):
    source: str
    external_id: str
    title: str
    company: str
    location: str
    remote: bool
    url: str
    description: str
    tags: list[str] = []
    created_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""  # "year" | "month" | "hour" | ""
    salary_is_predicted: bool = False  # Adzuna-specific: estimated, not stated
