"""Operator assistant (Requirement 13).

The assistant answers questions *about* the operational picture. It cannot
change anything: no tool here mutates state. Every answer carries the safety
disclaimer and, where the underlying data is unverified, says so rather than
presenting a guess as fact.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session
from app.core.constants import DISCLAIMER
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["assistant"])


@router.post("")
async def ask(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(get_actor),
) -> dict[str, Any]:
    """Answer an operator question from the live picture."""
    message = str(payload.get("message") or "").strip()
    if not message:
        return {
            "answer": "Please provide a message.",
            "citations": [],
            "disclaimer": DISCLAIMER,
        }
    result = await ChatService(session).answer(
        message,
        conversation_id=payload.get("conversation_id"),
        actor_role=actor["role"],
    )
    return {**result, "disclaimer": DISCLAIMER}


@router.get("/suggestions")
async def suggestions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Example questions the UI can offer."""
    return {
        "suggestions": [
            "How many incidents are unverified?",
            "Which shelters are near capacity?",
            "What resources are available right now?",
            "What is the current status of plan PLN-?",
            "Which roads have no verified condition report?",
        ],
        "note": (
            "The assistant explains and summarises. It cannot approve plans, "
            "dispatch resources, or create incidents."
        ),
    }