"""Alerts and the live WebSocket stream (Requirements 5, 12)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, hub, require_commander
from app.models.enums import AlertSeverity, AlertType
from app.models.models import Alert
from app.schemas.common import AckRequest
from app.services.alert_service import AlertService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _payload(alert: Alert) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "incident_id": alert.incident_id,
        "alert_type": alert.alert_type.value,
        "severity": alert.severity.value,
        "message": alert.message,
        "context": alert.context or {},
        "is_active": alert.is_active,
        "acknowledged": alert.acknowledged,
        "acknowledged_by": alert.acknowledged_by,
        "acknowledged_at": (
            alert.acknowledged_at.isoformat() if alert.acknowledged_at else None
        ),
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


@router.get("")
async def list_alerts(
    session: AsyncSession = Depends(get_session),
    active_only: bool = Query(default=True),
    severity: AlertSeverity | None = Query(default=None),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    service = AlertService(session)
    alerts = await service.list_alerts(active_only=active_only, limit=limit)
    if severity is not None:
        alerts = [a for a in alerts if a.severity == severity]
    if incident_id:
        alerts = [a for a in alerts if a.incident_id == incident_id]
    return {
        "total": len(alerts),
        "active_count": await service.active_count(),
        "items": [_payload(a) for a in alerts],
    }


@router.post("/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: str,
    payload: AckRequest,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    alert = await AlertService(session).acknowledge(
        alert_id, actor["user"], note=payload.note
    )
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")
    await session.commit()
    await session.refresh(alert)
    await hub.broadcast(
        "alerts",
        {"type": "alert_acknowledged", "alert_id": alert_id, "by": actor["user"]},
    )
    return _payload(alert)


@router.post("/broadcast")
async def broadcast_alert(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Raise a manual alert (used by sensors, radio room, or the UI)."""
    alert_type = str(payload.get("alert_type") or AlertType.WEATHER_WORSENING.value)
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="An alert requires a message.")
    try:
        alert_type = AlertType(alert_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown alert_type '{alert_type}'. "
            f"Allowed: {[t.value for t in AlertType]}",
        ) from exc
    severity = AlertSeverity(str(payload.get("severity") or AlertSeverity.INFO.value))
    alert = await AlertService(session).raise_alert(
        alert_type,
        message,
        severity=severity,
        incident_id=payload.get("incident_id"),
        context=payload.get("context") or {},
    )
    await session.commit()
    if alert is not None:
        await session.refresh(alert)
        await hub.broadcast("alerts", {"type": "alert", **(_payload(alert))})
        return _payload(alert)
    return {"created": False, "reason": "An identical alert is already active."}


@router.websocket("/stream")
async def alert_stream(websocket: WebSocket) -> None:
    """Live alert feed.

    Push-only for the operator UI. The socket carries alerts and plan/decision
    events; it cannot be used to mutate state.
    """
    await hub.connect("alerts", websocket)
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "channel": "alerts",
                "at": datetime.now(timezone.utc).isoformat(),
                "note": "Live alerts and plan events. State changes require the REST API.",
            }
        )
        while True:
            # The client may ping; anything it sends is ignored for state changes.
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport level
        logger.debug("WebSocket closed: %s", exc)
    finally:
        hub.disconnect("alerts", websocket)