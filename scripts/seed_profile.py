"""Load profile.local.json (falling back to profile.example.json) into the
DB. Replaces any existing profile, single-user tool, so there is exactly
one row: this always reflects the current file, never accumulates stale
copies.

Run: .venv/bin/python scripts/seed_profile.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlmodel import Session, delete, select  # noqa: E402

from storage.db import engine, init_db  # noqa: E402
from storage.models import Education, Experience, Profile, Skill  # noqa: E402

LOCAL_PROFILE = ROOT / "profile.local.json"
EXAMPLE_PROFILE = ROOT / "profile.example.json"


def _with_parsed_dates(entry: dict) -> dict:
    # SQLModel table=True classes skip Pydantic coercion on __init__, so
    # ISO date strings from JSON need to be parsed to `date` by hand here.
    parsed = dict(entry)
    for key in ("start_date", "end_date"):
        if parsed.get(key):
            parsed[key] = date.fromisoformat(parsed[key])
    return parsed


def load_profile_data() -> dict:
    path = LOCAL_PROFILE if LOCAL_PROFILE.exists() else EXAMPLE_PROFILE
    print(f"Loading profile from {path.name}")
    return json.loads(path.read_text())


def seed(data: dict) -> None:
    init_db()
    with Session(engine) as session:
        # Update the existing Profile row in place rather than delete +
        # recreate. Two real bugs, caught by an independent Codex review
        # and reproduced live before this fix: (1) ResumeVersion has a
        # foreign key on profile_id with no cascade, so deleting Profile
        # once any resume snapshot exists throws an IntegrityError -
        # exactly ResumeVersion's own "never overwritten, so past
        # versions stay recoverable" design being violated by this
        # script; (2) the old version committed the delete, then the new
        # profile, then its children as three separate transactions - a
        # failure partway through (bad JSON, a bad date) left a deleted
        # or half-populated profile rather than preserving the working
        # one. Everything below is one transaction: it only commits once,
        # at the very end, so a failure rolls back to the prior state.
        profile = session.exec(select(Profile)).first()
        if profile is None:
            profile = Profile(full_name=data["full_name"])
            session.add(profile)
            session.flush()  # assigns profile.id without ending the transaction
        else:
            session.exec(delete(Skill).where(Skill.profile_id == profile.id))
            session.exec(delete(Experience).where(Experience.profile_id == profile.id))
            session.exec(delete(Education).where(Education.profile_id == profile.id))

        profile.full_name = data["full_name"]
        profile.location = data.get("location", "")
        profile.email = data.get("email", "")
        profile.phone = data.get("phone", "")
        profile.linkedin_url = data.get("linkedin_url", "")
        profile.github_url = data.get("github_url", "")
        profile.summary = data.get("summary", "")
        profile.languages = data.get("languages", "")
        session.add(profile)

        for skill in data.get("skills", []):
            session.add(Skill(profile_id=profile.id, **skill))
        for experience in data.get("experiences", []):
            session.add(Experience(profile_id=profile.id, **_with_parsed_dates(experience)))
        for education in data.get("educations", []):
            session.add(Education(profile_id=profile.id, **_with_parsed_dates(education)))

        session.commit()
        session.refresh(profile)

        print(
            f"Seeded profile #{profile.id} ({profile.full_name}) with "
            f"{len(data.get('skills', []))} skills, "
            f"{len(data.get('experiences', []))} experiences, "
            f"{len(data.get('educations', []))} educations."
        )


if __name__ == "__main__":
    seed(load_profile_data())
