"""Road / route-condition service (Requirements 4, 10).

The single place that writes a road condition. It enforces the provenance rule
that the whole system depends on:

* A condition reported by a road authority, field team or the police is stored
  with ``fact_status = known``. That is a fact.
* An AI/agent inference about an area's accessibility is **never** written to
  this table. It is returned as ``inferred_observation`` for display only.
* An unknown condition stays ``unknown`` - it is never silently treated as open.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import FactStatus, RouteStatus
from app.models.models import Road
from app.services.geo import haversine_km
from app.tools.routing_tool import get_route

logger = logging.getLogger(__name__)

__all__ = ["RoadService", "AUTHORITATIVE_SOURCES"]

#: Sources whose reports are treated as facts about a route condition.
AUTHORITATIVE_SOURCES: set[str] = {
    "authority",
    "road_authority",
    "transport_authority",
    "police",
    "field_team",
    "government",
    "survey",
}

#: Statuses a human may report. ``unknown`` is allowed (no information yet) but
#: ``open`` may only be asserted by a report, never inferred.
REPORTABLE_STATUSES: set[str] = {
    RouteStatus.OPEN.value,
    RouteStatus.PARTIALLY_BLOCKED.value,
    RouteStatus.CLOSED.value,
    RouteStatus.IMPASSABLE.value,
    RouteStatus.UNKNOWN.value,
}


class RoadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- reads ---------------------------------------------------------------

    async def list_roads(self, zone: str | None = None) -> list[Road]:
        stmt = select(Road).order_by(Road.road_id)
        if zone:
            stmt = stmt.where(
                (Road.destination_zone == zone) | (Road.origin_zone == zone)
            )
        result = await self.session.scalars(stmt)
        return list(result)

    async def get_road(self, road_id: str) -> Road | None:
        return await self.session.scalar(select(Road).where(Road.road_id == road_id))

    async def known_closures(self) -> list[dict[str, Any]]:
        """Authoritative closures only - used to invalidate plans."""
        result = await self.session.scalars(
            select(Road).where(
                Road.status.in_([RouteStatus.CLOSED.value, RouteStatus.IMPASSABLE.value]),
                Road.fact_status == FactStatus.KNOWN.value,
            )
        )
        return [
            {
                "road_id": r.road_id,
                "name": r.name,
                "status": r.status,
                "destination_zone": r.destination_zone,
                "origin_zone": r.origin_zone,
                "reported_by": r.reported_by,
                "reported_at": r.reported_at,
                "blocked_reason": r.blocked_reason,
            }
            for r in result
        ]

    # -- writes --------------------------------------------------------------

    async def report_condition(
        self,
        road_id: str,
        *,
        status: str,
        reported_by: str,
        blocked_reason: str | None = None,
        fact_status: str | None = None,
        source: str = "authority",
    ) -> dict[str, Any]:
        """Record a human-reported road condition.

        Raises ``ValueError`` when the input would put a non-fact into the
        authoritative table (for example an ``open`` status asserted by an
        automated inference).
        """
        if status not in REPORTABLE_STATUSES:
            raise ValueError(
                f"Unsupported route status '{status}'. "
                f"Allowed: {sorted(REPORTABLE_STATUSES)}"
            )
        if not reported_by:
            raise ValueError("A road condition must name the reporting source.")

        road = await self.get_road(road_id)
        if road is None:
            raise LookupError(f"Road '{road_id}' is not in the registry.")

        authoritative = source in AUTHORITATIVE_SOURCES and reported_by.lower() not in {
            "agent", "ai", "inferred", "model", "system"
        }
        resolved_fact = (
            FactStatus(fact_status)
            if fact_status
            else (FactStatus.KNOWN if authoritative else FactStatus.UNVERIFIED)
        )

        if resolved_fact == FactStatus.KNOWN and not authoritative:
            raise ValueError(
                f"'{reported_by}' is not an authoritative source, so this condition "
                "is stored as unverified. Only a road authority, police or field "
                "team report can create a known fact."
            )
        if status == RouteStatus.OPEN.value and resolved_fact != FactStatus.KNOWN:
            raise ValueError(
                "A route may only be declared OPEN on the record of an "
                "authoritative report. Until then it stays unknown - unknown is "
                "never treated as open."
            )

        # Invariant: an unknown status means nobody has reported on this road.
        # Pairing it with 'unverified' would imply a report exists that has not
        # been confirmed, which is a different and wrong statement.
        if status == RouteStatus.UNKNOWN.value:
            resolved_fact = FactStatus.UNKNOWN

        previous = road.status
        road.status = status
        road.fact_status = resolved_fact
        road.reported_by = reported_by
        road.reported_at = datetime.now(timezone.utc)
        if blocked_reason:
            road.blocked_reason = blocked_reason
        await self.session.commit()
        await self.session.refresh(road)

        plan_impact = await self.assess_plan_impact(road)
        logger.info(
            "Road %s condition %s -> %s (%s)",
            road_id, previous, status, resolved_fact.value,
        )
        return {
            "road_id": road.road_id,
            "previous_status": previous,
            "status": status,
            "fact_status": resolved_fact.value,
            "is_fact": resolved_fact == FactStatus.KNOWN,
            "reported_by": reported_by,
            "reported_at": road.reported_at.isoformat() if road.reported_at else None,
            "blocked_reason": road.blocked_reason,
            "plan_impact": plan_impact,
            "note": (
                "Verified report from an authoritative source."
                if resolved_fact == FactStatus.KNOWN
                else "Unverified report - the condition must be field-confirmed."
            ),
        }

    # -- consequences --------------------------------------------------------

    async def assess_plan_impact(self, road: Road) -> dict[str, Any]:
        """Decide whether an active plan must be re-generated."""
        from app.models.models import Incident, ResponsePlan

        plans = list(
            await self.session.scalars(
                select(ResponsePlan).where(
                    ResponsePlan.status.in_(["draft", "awaiting_approval", "active"])
                )
            )
        )
        affected: list[dict[str, Any]] = []
        for plan in plans:
            # A plan references incidents by id; a road matters to the plan when
            # it serves the zone one of those incidents is in.
            for incident_id in plan.incident_ids or []:
                incident = await self.session.scalar(
                    select(Incident).where(Incident.incident_id == incident_id)
                )
                if incident is None:
                    continue
                zone = incident.location
                if zone and zone in {road.origin_zone, road.destination_zone}:
                    affected.append(
                        {
                            "plan_id": plan.plan_id,
                            "incident_id": incident.incident_id,
                            "zone": zone,
                            "reason": (
                                f"Access route for {zone} is now '{road.status}'"
                            ),
                        }
                    )
        return {
            "replan_required": bool(affected),
            "affected_plans": affected,
            "note": (
                "A known closure change to a route serving an active plan requires "
                "the plan to be regenerated and re-approved by a human. The system "
                "does not auto-approve the new plan."
                if affected
                else "No active plan depends on this route."
            ),
        }

    async def route_from(
        self,
        road: Road,
        destination: tuple[float, float],
        mode: str = "road",
    ) -> dict[str, Any]:
        """Routing detail for a specific road record (fact + engine estimate)."""
        detail = await get_route(
            (road.latitude, road.longitude) if road.latitude and road.longitude else None,
            destination,
            mode=mode,
        )
        straight = (
            haversine_km(road.latitude, road.longitude, destination[0], destination[1])
            if road.latitude and road.longitude
            else None
        )
        return {
            "road_id": road.road_id,
            "reported_status": road.status,
            "fact_status": road.fact_status,
            "is_fact": road.fact_status == FactStatus.KNOWN,
            "reported_by": road.reported_by,
            "blocked_reason": road.blocked_reason,
            "routing": detail,
            "straight_line_km": round(straight, 3) if straight is not None else None,
            "note": (
                "The reported status is authoritative; the routing engine's result is "
                "an independent estimate and does not override it."
            ),
        }