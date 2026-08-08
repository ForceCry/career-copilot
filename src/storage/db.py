from pathlib import Path

from sqlmodel import Session, create_engine

from ..settings import settings
from . import models  # noqa: F401 - registers tables on SQLModel.metadata

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

engine = create_engine(settings.mysql_url, pool_pre_ping=True)


def init_db() -> None:
    # Schema is Alembic-managed (see alembic/versions/) rather than
    # SQLModel.metadata.create_all() - a previous round of manual
    # ALTER TABLE statements for schema changes (e.g. adding
    # embedding_queued_at) was error-prone and undocumented; upgrading to
    # "head" on every startup keeps a fresh clone and an existing
    # deployment on the same schema without a separate manual step.
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.mysql_url)
    command.upgrade(cfg, "head")


def get_session():
    with Session(engine) as session:
        yield session
