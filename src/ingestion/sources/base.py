from abc import ABC, abstractmethod

from ..models import Vacancy


class VacancySource(ABC):
    """Common interface every job source implements, so the pipeline
    doesn't care whether data comes from Adzuna, Arbeitnow, or the next
    one we bolt on."""

    name: str

    @abstractmethod
    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        ...
