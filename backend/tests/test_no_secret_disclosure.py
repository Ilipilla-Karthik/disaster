"""TC-07: no endpoint may disclose a secret.

Written after ``/health`` was found returning the full SQLAlchemy DSN on a public
HTTPS endpoint, which published the production database password. ``/health`` is
deliberately unauthenticated so uptime checks work, and it deliberately reports
which database is in use, so neither fact is a defence on its own - the
diagnostic has to stay useful while the credential inside it stays hidden.

These tests scan responses for the *actual* secret values held in the
environment, so they fail if any endpoint leaks any configured key, not just the
database password.
"""

from __future__ import annotations

import pytest

from app.db.session import safe_url

# Values that would be catastrophic to publish. Collected from the environment
# so the assertions track whatever is actually configured.
_SECRET_ENV_VARS = (
    "DATABASE_URL",
    "SECRET_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "WEATHER_API_KEY",
    "MAPBOX_API_KEY",
)


def _configured_secrets() -> list[tuple[str, str]]:
    import os

    found: list[tuple[str, str]] = []
    for var in _SECRET_ENV_VARS:
        raw = os.environ.get(var, "").strip()
        if not raw or raw.startswith("change-this"):
            continue
        found.append((var, raw))
        # The password component matters as much as the whole DSN.
        if "://" in raw and "@" in raw:
            creds = raw.split("://", 1)[1].split("@", 1)[0]
            if ":" in creds:
                password = creds.split(":", 1)[1]
                if len(password) >= 6:
                    found.append((f"{var} (password)", password))
    return found


# ---- unit: the redaction helper ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgresql+asyncpg://disaster:hunter2@db.host:5432/disaster_response",
            "postgresql+asyncpg://disaster:***@db.host:5432/disaster_response",
        ),
        # A password containing '@' must be consumed whole, not up to its first '@'.
        (
            "postgresql+asyncpg://user:p@ss:word@host/db",
            "postgresql+asyncpg://user:***@host/db",
        ),
        # No password at all: leave the URL untouched.
        (
            "postgresql+asyncpg://disaster@host:5432/db",
            "postgresql+asyncpg://disaster@host:5432/db",
        ),
        # SQLite has no credentials to redact.
        ("sqlite+aiosqlite:///./disaster_response.db", "sqlite+aiosqlite:///./disaster_response.db"),
    ],
)
def test_safe_url_redacts_password(raw: str, expected: str) -> None:
    assert safe_url(raw) == expected


def test_safe_url_keeps_useful_diagnostic_detail() -> None:
    """Redaction must not reduce the endpoint to a useless yes/no."""
    redacted = safe_url("postgresql+asyncpg://disaster:hunter2@db.host:5432/disaster_response")
    assert "db.host" in redacted
    assert "5432" in redacted
    assert "disaster_response" in redacted


# ---- integration: the live endpoints -------------------------------------------

_UNAUTHENTICATED_ENDPOINTS = ("/", "/health", "/integrations/status", "/safety/policy")


@pytest.mark.parametrize("path", _UNAUTHENTICATED_ENDPOINTS)
async def test_public_endpoints_do_not_leak_configured_secrets(
    client, anonymous_client, running_app, path
) -> None:
    """No public endpoint may echo any configured secret back."""
    secrets = _configured_secrets()
    if not secrets:
        pytest.skip("no secrets configured in this environment")

    for http_client in (anonymous_client, client):
        response = await http_client.get(path)
        body = response.text
        for label, value in secrets:
            assert value not in body, f"{path} leaked {label}"


@pytest.fixture
def postgres_dsn(monkeypatch, running_app):
    """Pretend the engine is bound to a credentialed PostgreSQL DSN.

    The suite runs on SQLite, where the DSN has no password and the leak this
    module guards against cannot occur. Without this substitution the
    integration test below would be permanently inert, so it is explicitly
    steered into the state that actually breaks in production.
    """
    from app.db import session as db_session

    dsn = "postgresql+asyncpg://disaster:L3akedDbPw12345@db.example.com:5432/disaster_response"
    monkeypatch.setattr(db_session, "ASYNC_URL", dsn)
    monkeypatch.setattr(db_session, "USING_SQLITE", False)
    return dsn


async def test_health_does_not_leak_the_database_password(anonymous_client, postgres_dsn) -> None:
    """The exact bug: /health returned the raw DSN, password included."""
    response = await anonymous_client.get("/health")
    body = response.text

    assert "L3akedDbPw12345" not in body
    # ...and the diagnostic contract must survive the redaction.
    database = response.json()["database"]
    assert "db.example.com" in database["in_use"]
    assert ":***@" in database["in_use"]