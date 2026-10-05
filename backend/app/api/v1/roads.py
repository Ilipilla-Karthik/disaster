"""Road conditions and routing (Requirements 4, 10).

The provenance contract is enforced here and in ``RoadService``:

* ``known``    - reported by an authoritative source (fact).
* ``inferred`` - an agent hypothesis, returned as ``inferred_observation``
  and never written to the roads table.
* ``unknown``  - no information. Not open.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, require_commander
from app.models.enums import AlertSeverity, AlertType, RouteStatus
from app.services.alert_service import AlertService
from app.services.road_service import RoadService
from app.tools.routing_tool import get_route

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roads", tags=["roads"])


class RoadConditionReport(BaseModel):
    """A human report of a road's condition."""

    status: RouteStatus
    reported_by: str = Field(min_length=1, max_length=128)
    blocked_reason: str | None = Field(default=None, max_length=500)
    source: str = Field(default="authority", max_length=64)


class RouteQuery(BaseModel):
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)
    dest_lat: float | None = Field(default=None, ge=-90, le=90)
    dest_lon: float | None = Field(default=None, ge=-180, le=180)
    mode: str = Field(default="road")


def _payload(road: Any) -> dict[str, Any]:
    return {
        "road_id": road.road_id,
        "name": road.name,
        "location": road.location,
        "origin_zone": road.origin_zone,
        "destination_zone": road.destination_zone,
        "latitude": road.latitude,
        "longitude": road.longitude,
        "length_km": road.length_km,
        "status": road.status.value,
        "fact_status": road.fact_status.value,
        "is_fact": road.fact_status.value == "known",
        "blocked_reason": road.blocked_reason,
        "reported_by": road.reported_by,
        "reported_at": road.reported_at.isoformat() if road.reported_at else None,
        "access_mode": road.access_mode.value,
    }


@router.get("")
async def list_roads(
    session: AsyncSession = Depends(get_session),
    zone: str | None = Query(default=None),
) -> dict[str, Any]:
    roads = await RoadService(session).list_roads(zone=zone)
    return {
        "total": len(roads),
        "items": [_payload(r) for r in roads],
        "legend": {
            "known": "Authoritative report - a fact",
            "inferred": "Agent hypothesis - not a fact",
            "unverified": "Reported by a non-authoritative source",
            "unknown": "No report on file. Unknown is never treated as open.",
        },
    }


@router.get("/closures")
async def known_closures(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    closures = await RoadService(session).known_closures()
    return {
        "count": len(closures),
        "items": closures,
        "note": (
            "Only closures reported by an authoritative source appear here. Agent "
            "inferences are never promoted to this list."
        ),
    }


@router.get("/{road_id}")
async def get_road(road_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    road = await RoadService(session).get_road(road_id)
    if road is None:
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found")
    return _payload(road)


@router.post("/{road_id}/condition")
async def report_condition(
    road_id: str,
    payload: RoadConditionReport,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Record a road condition reported by a human.

    Declaring a route ``open`` requires an authoritative report; anything else
    is stored as unverified so the map keeps showing it as unconfirmed.
    """
    service = RoadService(session)
    try:
        result = await service.report_condition(
            road_id,
            status=payload.status.value,
            reported_by=payload.reported_by or actor["user"],
            blocked_reason=payload.blocked_reason,
            source=payload.source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if result["status"] in {RouteStatus.CLOSED.value, RouteStatus.IMPASSABLE.value}:
        road = await service.get_road(road_id)
        await AlertService(session).raise_alert(
            AlertType.ROUTE_BLOCKED,
            f"{road_id} ({road.name}) reported as {result['status']}"
            + (f": {payload.blocked_reason}" if payload.blocked_reason else ""),
            severity=AlertSeverity.CRITICAL,
            context={"road_id": road_id, "reported_by": result["reported_by"]},
        )
    if result["plan_impact"]["replan_required"]:
        await AlertService(session).raise_alert(
            AlertType.REPLAN_TRIGGERED,
            f"Road condition change on {road_id} affects active plan(s): "
            + ", ".join(a["plan_id"] for a in result["plan_impact"]["affected_plans"]),
            severity=AlertSeverity.WARNING,
            context=result["plan_impact"]["affected_plans"],
        )
        await AlertService(session).raise_alert(
            AlertType.APPROVAL_REQUIRED,
            f"Plan(s) affected by {road_id} must be regenerated and re-approved by a "
            "human before any re-deployment.",
            severity=AlertSeverity.INFO,
        )
    await session.commit()
    return result


@router.post("/route")
async def route_check(payload: RouteQuery) -> dict[str, Any]:
    """Routing between two points, with provenance on the result."""
    if None in (payload.origin_lat, payload.origin_lon, payload.dest_lat, payload.dest_lon):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both origin and destination coordinates are required.",
        )
    result = await get_route(
        (payload.origin_lat, payload.origin_lon),
        (payload.dest_lat, payload.dest_lon),
        mode=payload.mode,
    )
    return {
        **result,
        "provenance_note": (
            "A routing engine result is a computational estimate, not an "
            "observation of road conditions. Where no route can be verified the "
            "answer is 'verification required' - the system does not substitute a "
            "different route."
        ),
    }