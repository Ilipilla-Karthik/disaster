"""FastAPI application entrypoint.

Startup sequence, and why it is ordered this way:

1. Check the configured database. If it is unreachable and
   ``DB_FALLBACK_TO_SQLITE`` is on, rebind the engine to SQLite *before* any
   session is created, so every request sees a working database.
2. Create tables.
3. Seed the synthetic operational dataset (idempotent).

The app refuses to pretend it is healthy: ``/health`` reports the database
actually in use, and the integration status endpoint reports which services are
live versus simulated.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import hub
from app.api.v1.router import api_router
from app.core.config import settings
from app.db import session as db_session
from app.orchestration.graph import AGENT_REGISTRY
from app.tools.llm_tool import llm_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("app")


async def _prepare_database() -> dict[str, Any]:
    """Connect, fall back if permitted, create tables, seed."""
    info: dict[str, Any] = {"requested": db_session.ASYNC_URL, "fallback_used": False}

    if not await db_session.verify_connection():
        if settings.DB_FALLBACK_TO_SQLITE:
            logger.warning(
                "Configured database unreachable; falling back to SQLite so the "
                "system remains demonstrable. Set DB_FALLBACK_TO_SQLITE=False to "
                "fail fast instead."
            )
            db_session.use_sqlite_fallback()
            info["fallback_used"] = True
        else:
            logger.error("Configured database unreachable and fallback disabled.")
            info["available"] = False
            return info

    if settings.AUTO_CREATE_TABLES:
        await db_session.init_db()
        info["schema"] = "created"
    if settings.AUTO_SEED:
        from app.db.seed import seed_all

        async with db_session.AsyncSessionLocal() as session:
            info["seed"] = await seed_all(session)
    info["available"] = True
    info["in_use"] = db_session.ASYNC_URL
    return info


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    info = await _prepare_database()
    app.state.database = info
    logger.info(
        "Startup complete: database=%s fallback=%s",
        info.get("in_use"),
        info.get("fallback_used"),
    )
    yield
    await db_session.engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "api": settings.API_V1_STR,
        "disclaimer": (
            "Decision-support only. All outputs are recommendations requiring "
            "authorised human approval; the system never dispatches resources."
        ),
    }


@app.get("/health", tags=["meta"])
async def health() -> JSONResponse:
    """Report what is actually reachable, not what is configured."""
    db_ok = await db_session.verify_connection()
    info = getattr(app.state, "database", {}) or {}
    payload = {
        "status": "ok" if db_ok else "degraded",
        "version": settings.VERSION,
        "database": {
            "in_use": db_session.ASYNC_URL,
            "requested": info.get("requested", db_session.ASYNC_URL),
            "reachable": db_ok,
            "fallback_used": info.get("fallback_used", False),
            "sqlite": db_session.USING_SQLITE,
        },
        "websocket_clients": hub.channels,
        "disclaimer": (
            "Decision-support only. All outputs are recommendations requiring "
            "authorised human approval."
        ),
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)


@app.get("/integrations/status", tags=["meta"])
async def integration_status() -> dict[str, Any]:
    """Live vs simulated for every external dependency.

    Simulation is a supported mode, not a hidden one: a simulated weather feed
    propagates a ``simulation_mode`` label into every agent output that used it.
    """
    from app.tools.geocoding_tool import geocoding_status
    from app.tools.routing_tool import routing_status
    from app.tools.weather_tool import weather_status

    return {
        "weather": weather_status(),
        "routing": routing_status(),
        "geocoding": geocoding_status(),
        "llm": llm_status(),
        "agents": sorted(AGENT_REGISTRY),
        "note": (
            "Where a service is simulated, the output is labelled as such and the "
            "fallback behaviour is deterministic and documented."
        ),
    }


@app.get("/safety/policy", tags=["meta"])
async def safety_policy() -> dict[str, Any]:
    """The system's non-negotiable constraints, served as data.

    Exposing these makes them checkable by the operator UI and by tests.
    """
    from app.agents.base import BaseAgent
    from app.core.constants import DISCLAIMER

    return {
        "disclaimer": DISCLAIMER,
        "prohibitions": list(BaseAgent.PROHIBITIONS),
        "human_gates": [
            "Plan approval requires an identified commander or officer (X-Role).",
            "Approval moves units to reserved; deployment is a separate explicit action.",
            "Agent output is never treated as an order.",
        ],
        "provenance_rules": [
            "Only authority-reported facts are stored as known.",
            "Agent inferences are returned as inferred_observation and never persisted as fact.",
            "Unknown is never treated as open.",
            "Simulated data is labelled simulation_mode wherever it is used.",
        ],
    }