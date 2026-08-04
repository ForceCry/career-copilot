from datetime import datetime
from typing import Optional

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
    created_at: Optional[datetime] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_period: str = ""  # "year" | "month" | "hour" | ""
    salary_is_predicted: bool = False  # Adzuna-specific: estimated, not stated
