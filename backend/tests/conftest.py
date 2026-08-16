"""Test fixtures.

Every test module that touches the database gets a throwaway SQLite file, so
tests never see (or corrupt) a developer's working data.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Must be set before app.core.config is imported anywhere.
_TEST_DB = Path(tempfile.gettempdir()) / "mii_test.db"
# Start from a clean database every session: a schema left behind by an
# aborted run would make failures look like application bugs.
for _suffix in ("", "-wal", "-shm"):
    _leftover = Path(str(_TEST_DB) + _suffix)
    if _leftover.exists():
        _leftover.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB.as_posix()}")
os.environ.setdefault("APP_ENV", "DEMO")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("QUOTE_PROVIDERS", "demo")
os.environ.setdefault("HISTORY_PROVIDERS", "demo")
os.environ.setdefault("OPTION_CHAIN_PROVIDERS", "demo")
os.environ.setdefault("NEWS_PROVIDERS", "demo")
os.environ.setdefault("IPO_PROVIDERS", "demo")


@pytest.fixture(scope="session")
def app_client():
    """A TestClient with the full lifespan run once (schema + demo seed)."""
    from fastapi.testclient import TestClient

    import app.main as main

    with TestClient(main.app) as client:
        yield client


@pytest.fixture(scope="session")
def db_session_factory():
    from app.db.session import SessionLocal

    return SessionLocal


@pytest.fixture()
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def admin_token(app_client):
    """Registers the first user, which the API promotes to ADMIN."""
    response = app_client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "a-sufficiently-long-password",
        "display_name": "Test Admin",
    })
    if response.status_code == 201:
        return response.json()["access_token"]

    login = app_client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "a-sufficiently-long-password",
    })
    return login.json()["access_token"]


@pytest.fixture(scope="session")
def user_token(app_client, admin_token):
    """A second account, which is a plain USER."""
    response = app_client.post("/api/auth/register", json={
        "email": "user@example.com",
        "password": "another-long-enough-password",
        "display_name": "Test User",
    })
    if response.status_code == 201:
        return response.json()["access_token"]
    login = app_client.post("/api/auth/login", json={
        "email": "user@example.com",
        "password": "another-long-enough-password",
    })
    return login.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
