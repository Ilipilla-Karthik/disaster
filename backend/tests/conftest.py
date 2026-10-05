"""Shared pytest fixtures.

Every test runs against a throwaway SQLite file. The database URL is set before
``app.db.session`` is imported, because the engine is built at import time.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# ``app.core.config`` instantiates Settings at import time, and importing it
# happens during test collection - before any fixture runs. The database
# environment therefore has to be set here, at conftest import, or the suite
# silently binds to the default ./disaster_response.db and every run inherits
# the previous run's rows.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="drs-tests-"))
DB_PATH = _TMP_DIR / "test.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
os.environ["SQLITE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["AUTO_SEED"] = "true"
os.environ["DB_FALLBACK_TO_SQLITE"] = "true"
os.environ.pop("WEATHER_API_KEY", None)
# Tests must never wait on external HTTP. Each integration already has a
# documented simulation fallback; these timeouts just stop an unreachable
# network from stalling the suite.
os.environ["WEATHER_API_TIMEOUT"] = "0.25"
os.environ["ROUTING_API_TIMEOUT"] = "0.25"
os.environ["GEOCODING_API_TIMEOUT"] = "0.25"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def db_path() -> Path:
    return DB_PATH


@pytest.fixture(scope="session")
def app_module():
    import main

    return main


@pytest.fixture(scope="session")
async def running_app(app_module):
    """Start the application lifespan exactly once for the whole session.

    Entering the lifespan per test re-runs schema creation, seeding and the
    external connectivity probes each time, which dominates the runtime.
    """
    async with app_module.app.router.lifespan_context(app_module.app):
        yield app_module.app


def _make_client(app, headers):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
    )


@pytest.fixture
async def client(running_app):
    """Commander identity. Mutations are permitted."""
    async with _make_client(
        running_app, {"X-User": "chief.morales", "X-Role": "commander"}
    ) as http_client:
        yield http_client


@pytest.fixture
async def anonymous_client(running_app):
    """No identity headers - operational actions must be refused."""
    async with _make_client(running_app, {}) as http_client:
        yield http_client