import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401 - registers tables on SQLModel.metadata

DB_PATH = Path(os.environ.get("DB_PATH", "data/career_copilot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
