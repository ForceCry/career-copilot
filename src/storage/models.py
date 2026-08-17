from datetime import UTC, date, datetime

from sqlalchemy import Column, Text
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Deliberately plain strings, not a DB-level enum - matches the existing
# convention in this file (salary_period, Skill.category), and keeps
# adding a status as simple as updating this tuple + the UI, no migration
# needed. Validated at the repo layer (application_repo.set_status), not
# by the DB.
APPLICATION_STATUSES = ("saved", "applied", "interviewing", "offer", "rejected", "dismissed")

# Shared between recommendations filtering (main.py) and company-level
# exclusion feedback (application_repo.get_excluded_companies) - a single
# source of truth for "the user is done with this" so the two can't drift
# out of sync with each other.
NEGATIVE_APPLICATION_STATUSES = {"dismissed", "rejected"}


class Profile(SQLModel, table=True):
    """Single-user tool: in practice there's exactly one row here, but
    modeling it as a real table (not a config singleton) keeps the door
    open for resume variants per target role later."""

    id: int | None = Field(default=None, primary_key=True)
    full_name: str
    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    summary: str = Field(default="", sa_column=Column(Text))
    languages: str = ""  # "English (B1-B2), Ukrainian (Native)"

    skills: list["Skill"] = Relationship(back_populates="profile")
    experiences: list["Experience"] = Relationship(back_populates="profile")
    educations: list["Education"] = Relationship(back_populates="profile")
    resume_versions: list["ResumeVersion"] = Relationship(back_populates="profile")


class Skill(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    name: str
    category: str = ""  # "language" | "framework" | "tool" | ...

    profile: Profile = Relationship(back_populates="skills")


class Experience(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    title: str
    company: str
    location: str = ""
    start_date: date
    end_date: date | None = None  # None = current position
    highlights: str = Field(default="", sa_column=Column(Text))  # newline-separated bullet points

    profile: Profile = Relationship(back_populates="experiences")


class Education(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    institution: str
    degree: str
    field: str = ""
    start_date: date | None = None
    end_date: date | None = None

    profile: Profile = Relationship(back_populates="educations")


class ResumeVersion(SQLModel, table=True):
    """A generated/edited resume snapshot. New versions are appended, never
    overwritten in place, so past tailored versions stay recoverable."""

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    label: str = ""
    content_html: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utcnow)
    is_active: bool = False

    profile: Profile = Relationship(back_populates="resume_versions")


class VacancyRecord(SQLModel, table=True):
    """Persisted counterpart to ingestion.models.Vacancy (that one stays a
    plain DTO for what a source just fetched; this one is what's actually
    stored). Upserted by (source, external_id) - re-ingesting a still-open
    posting updates last_seen_at rather than creating a duplicate."""

    __tablename__ = "vacancy"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_vacancy_source_external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str
    title: str
    company: str
    location: str = ""
    remote: bool = False
    url: str = Field(sa_column=Column(Text))
    description: str = Field(default="", sa_column=Column(Text))
    tags: str = Field(default="", sa_column=Column(Text))  # comma-separated - no native array type
    posted_at: datetime | None = None  # from the source, if it provides one
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    salary_period: str = ""  # "year" | "month" | "hour" | ""
    salary_is_predicted: bool = False
    first_seen_at: datetime = Field(default_factory=_utcnow)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    # NULL means "not confirmed queued for embedding yet" - set only after
    # RabbitMQ actually confirms receiving the publish (see
    # messaging/rabbitmq.py), not just after upsert_vacancies decides it
    # should be queued. A publish that silently fails (flagged by an
    # independent Codex review: DB commit and queue publish were separate,
    # non-recoverable steps) leaves this NULL, so the next ingest run -
    # even an otherwise-unchanged one - naturally re-queues it instead of
    # requiring someone to remember to run the backfill script.
    embedding_queued_at: datetime | None = None


class Application(SQLModel, table=True):
    """The user's relationship to a vacancy - separate from VacancyRecord
    itself, since a vacancy can exist (ingested, scored, recommended)
    without ever being tracked; only vacancies the user has acted on
    (saved, applied, dismissed, ...) get a row here. One row per vacancy;
    ApplicationEvent below is the append-only history that makes past
    transitions (when did this move to interviewing, before it was
    rejected) recoverable - same "never overwritten in place" pattern as
    ResumeVersion."""

    __tablename__ = "application"

    id: int | None = Field(default=None, primary_key=True)
    vacancy_id: int = Field(foreign_key="vacancy.id", unique=True)
    status: str = Field(index=True)  # one of APPLICATION_STATUSES
    notes: str = Field(default="", sa_column=Column(Text))
    follow_up_at: date | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # order_by=id, not created_at - the migration's MySQL DATETIME column
    # has no fractional-second precision, so several transitions inside
    # the same second (routine when clicking through a pipeline quickly)
    # share a created_at and would sort arbitrarily without a tie-breaker.
    # id is monotonic with insertion order regardless. Flagged by an
    # independent Codex review.
    events: list["ApplicationEvent"] = Relationship(
        back_populates="application",
        sa_relationship_kwargs={"order_by": "ApplicationEvent.id"},
    )


class ApplicationEvent(SQLModel, table=True):
    """One row per status transition - append-only, never edited or
    deleted, so the timeline (saved -> applied -> interviewing ->
    rejected, with dates) stays reconstructable even though Application
    itself only holds the current status."""

    __tablename__ = "application_event"

    id: int | None = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    status: str
    note: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utcnow)

    application: Application = Relationship(back_populates="events")


ARTIFACT_TYPES = ("cover_letter", "tailoring_suggestions")


class GeneratedArtifact(SQLModel, table=True):
    """An LLM-generated cover letter or resume-tailoring suggestion for a
    specific vacancy - append-only, never overwritten in place, same
    pattern as ResumeVersion/ApplicationEvent, so regenerating (the
    underlying profile changed, or just wanting a second attempt) doesn't
    lose a version someone might still want to compare against. Keyed to
    vacancy_id directly rather than application_id - generating one of
    these doesn't require the vacancy to be tracked in the applications
    pipeline at all (e.g. previewing a cover letter before deciding
    whether to apply).

    vacancy_title/company/url are a denormalized snapshot taken at
    generation time, not a live join to VacancyRecord - flagged by an
    independent Codex review: VacancyRecord's fields get overwritten in
    place on re-ingestion (upsert_vacancies refreshes title/company/url/
    description for a still-open posting), so a letter viewed weeks later
    against the CURRENT vacancy row could appear to be about a different
    company or link than what it was actually written for. The generated
    content itself is a snapshot for the same reason (already true before
    this fix) - the display context needs to match it."""

    __tablename__ = "generated_artifact"

    id: int | None = Field(default=None, primary_key=True)
    vacancy_id: int = Field(foreign_key="vacancy.id", index=True)
    artifact_type: str = Field(index=True)  # one of ARTIFACT_TYPES
    content: str = Field(sa_column=Column(Text))
    vacancy_title: str
    vacancy_company: str
    vacancy_url: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=_utcnow)
