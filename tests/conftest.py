import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from src.main import app  # noqa: E402
from src.storage.db import get_session  # noqa: E402


@pytest.fixture
def test_engine():
    # StaticPool forces every checkout to reuse the same underlying
    # connection - without it, a plain "sqlite:///:memory:" engine hands
    # out a fresh, empty in-memory database to each new connection, and
    # FastAPI runs sync path operations in a worker thread (via
    # anyio.to_thread), so different requests/dependencies would silently
    # see different, disconnected databases.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(test_engine):
    with Session(test_engine) as s:
        yield s


@pytest.fixture
def client(test_engine, session):
    def _get_session_override():
        yield session

    app.dependency_overrides[get_session] = _get_session_override
    # /health talks to src.main.engine directly (not via the overridable
    # get_session dependency), and the startup handler's init_db() would
    # otherwise create tables against the real MySQL engine (built from
    # .env at import time) - both patched to the same in-memory test
    # engine/no-op so tests never touch a real database.
    with patch("src.main.engine", test_engine), patch("src.main.init_db"), TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
