from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Profile(SQLModel, table=True):
    """Single-user tool: in practice there's exactly one row here, but
    modeling it as a real table (not a config singleton) keeps the door
    open for resume variants per target role later."""

    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str
    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    summary: str = ""
    languages: str = ""  # "English (B1-B2), Ukrainian (Native)"

    skills: list["Skill"] = Relationship(back_populates="profile")
    experiences: list["Experience"] = Relationship(back_populates="profile")
    educations: list["Education"] = Relationship(back_populates="profile")
    resume_versions: list["ResumeVersion"] = Relationship(back_populates="profile")


class Skill(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    name: str
    category: str = ""  # "language" | "framework" | "tool" | ...

    profile: Profile = Relationship(back_populates="skills")


class Experience(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    title: str
    company: str
    location: str = ""
    start_date: date
    end_date: Optional[date] = None  # None = current position
    highlights: str = ""  # newline-separated bullet points

    profile: Profile = Relationship(back_populates="experiences")


class Education(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    institution: str
    degree: str
    field: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    profile: Profile = Relationship(back_populates="educations")


class ResumeVersion(SQLModel, table=True):
    """A generated/edited resume snapshot. New versions are appended, never
    overwritten in place, so past tailored versions stay recoverable."""

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")
    label: str = ""
    content_html: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = False

    profile: Profile = Relationship(back_populates="resume_versions")
