import os

from sqlmodel import Session, SQLModel, create_engine

from . import models  # noqa: F401 - registers tables on SQLModel.metadata


def _build_url() -> str:
    user = os.environ.get("MYSQL_USER", "career_copilot")
    password = os.environ.get("MYSQL_PASSWORD", "career_copilot")
    host = os.environ.get("MYSQL_HOST", "mysql")
    port = os.environ.get("MYSQL_PORT", "3306")
    database = os.environ.get("MYSQL_DATABASE", "career_copilot")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


engine = create_engine(_build_url(), pool_pre_ping=True)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
