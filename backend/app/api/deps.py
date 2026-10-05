"""Shared API dependencies.

Two things every route needs:

``get_session``
    A request-scoped async session.

``get_actor``
    The authenticated human. This is a prototype, so identity comes from
    headers rather than a full auth stack - but it is *never* optional for the
    operations that matter. ``require_commander`` guards anything that changes
    resource state: an unauthenticated request cannot approve a plan or start
    an action, which is what makes the human-in-the-loop gate meaningful.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, AsyncGenerator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session

logger = logging.getLogger(__name__)

__all__ = [
    "get_session",
    "get_actor",
    "require_commander",
    "CommandNotAuthorised",
    "CommandHub",
    "hub",
]


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped session.

    Resolved through the module at call time, not captured at import time: if
    startup fell back to SQLite the sessionmaker is rebound, and a route that
    held the original object would keep talking to an unreachable database.
    """
    async with db_session.AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


class CommandNotAuthorised(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


async def get_actor(
    x_user: str | None = Header(default=None, alias="X-User"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> dict[str, str]:
    """Identify the caller from headers (prototype auth)."""
    user = (x_user or "anonymous").strip() or "anonymous"
    role = (x_role or "viewer").strip().lower() or "viewer"
    return {"user": user, "role": role}


async def require_commander(
    actor: dict[str, str] = Depends(get_actor),
) -> dict[str, str]:
    """Guard for anything that changes operational state.

    Anonymous callers and non-commander roles are refused. This is what stops a
    browser refresh or a stray script from dispatching a resource.
    """
    if actor["user"] == "anonymous":
        raise CommandNotAuthorised(
            "This operation requires an identified officer. Send the X-User header."
        )
    if actor["role"] not in {"commander", "officer", "admin", "communications"}:
        raise CommandNotAuthorised(
            f"Role '{actor['role']}' may view but not change operational state. "
            "Plan approval and dispatch require a commander or officer role."
        )
    return actor


class CommandHub:
    """In-process WebSocket fan-out for live alert/plan events."""

    def __init__(self) -> None:
        self._clients: dict[str, set[Any]] = defaultdict(set)

    async def connect(self, channel: str, websocket: Any) -> None:
        await websocket.accept()
        self._clients[channel].add(websocket)

    def disconnect(self, channel: str, websocket: Any) -> None:
        self._clients[channel].discard(websocket)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        dead: list[Any] = []
        for client in list(self._clients.get(channel, ())):
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.disconnect(channel, client)

    @property
    def channels(self) -> dict[str, int]:
        return {name: len(subs) for name, subs in self._clients.items()}


hub = CommandHub()