from datetime import datetime

from pydantic import BaseModel


class Job(BaseModel):
    external_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    category: str = ""
    created_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""  # Adzuna's convention: "year" (annualized estimates)
    salary_is_predicted: bool = False
