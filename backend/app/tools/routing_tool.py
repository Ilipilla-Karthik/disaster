"""Routing & accessibility tool (Requirement 9, Agent 4).

Primary source: **OSRM** public demo server (OpenStreetMap data).
When unreachable, a documented great-circle estimate is returned with
``verified=False`` and ``source="fallback_estimate"`` so no agent can present a
computed time as a verified route.

Critical distinction enforced here (per the specification):

* A **known road closure** is a fact recorded by an authoritative source
  (``FactStatus.KNOWN``) and belongs in the ``Roads`` table.
* An **inferred accessibility problem** is the geospatial agent's *hypothesis*
  (``FactStatus.INFERRED``) - e.g. "no route found, so the area may be
  isolated". The two are returned in different fields and never merged.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.utils import enum_value, field_get
from app.models.enums import AccessMode, FactStatus, RouteStatus
from app.services.geo import estimate_travel_minutes, haversine_km

logger = logging.getLogger(__name__)

__all__ = ["RoutingTool", "get_route", "routing_status", "ACCESSIBILITY_LABELS"]

ACCESSIBILITY_LABELS: dict[str, str] = {
    RouteStatus.OPEN.value: "Open",
    RouteStatus.PARTIALLY_BLOCKED.value: "Partially blocked",
    RouteStatus.CLOSED.value: "Closed",
    RouteStatus.IMPASSABLE.value: "Impassable",
    RouteStatus.UNKNOWN.value: "Unknown - verification required",
}

# Default travel speed by access mode (km/h) for fallback estimates.
_MODE_SPEED_KMH: dict[str, float] = {
    AccessMode.ROAD.value: 40.0,
    AccessMode.WATER.value: 25.0,
    AccessMode.AIR.value: 200.0,
    AccessMode.UNKNOWN.value: 40.0,
}


class RoutingTool:
    """Distance / travel-time queries with an explicit verification flag."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ROUTING_API_BASE).rstrip("/")
        self.timeout = timeout or settings.ROUTING_API_TIMEOUT
        self._live_available: bool | None = None

    # -- public API ---------------------------------------------------------

    async def route(
        self,
        origin: tuple[float, float] | None,
        destination: tuple[float, float] | None,
        mode: str = AccessMode.ROAD.value,
        profile: str = "driving",
    ) -> dict[str, Any]:
        """Return a route estimate between two coordinates."""
        if not origin or not destination:
            return self._no_route(
                "missing_coordinates",
                "Cannot compute a route: incident or resource coordinates are missing. "
                "Route verification required.",
            )

        if self._live_available is not False:
            result = await self._fetch_osrm(origin, destination, profile)
            if result is not None:
                self._live_available = True
                result["mode"] = mode
                return result
            self._live_available = False

        return self._estimate(origin, destination, mode)

    def straight_line(
        self,
        origin: tuple[float, float] | None,
        destination: tuple[float, float] | None,
    ) -> float | None:
        if not origin or not destination:
            return None
        return round(haversine_km(origin[0], origin[1], destination[0], destination[1]), 3)

    # -- providers ----------------------------------------------------------

    async def _fetch_osrm(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        profile: str,
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/route/v1/{profile}/{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
        params = {"overview": "false", "alternatives": "false", "steps": "false"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("OSRM unavailable: %s", exc)
            return None

        if data.get("code") != "Ok" or not data.get("routes"):
            return None

        r = data["routes"][0]
        distance_km = round(float(r.get("distance", 0.0)) / 1000.0, 3)
        travel_minutes = round(float(r.get("duration", 0.0)) / 60.0, 1)
        return {
            "source": "osrm",
            "verified": True,
            "distance_km": distance_km,
            "travel_minutes": travel_minutes,
            "note": "Live OSRM route (OpenStreetMap data).",
        }

    def _estimate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> dict[str, Any]:
        distance = round(haversine_km(origin[0], origin[1], destination[0], destination[1]), 3)
        # Road distance is typically ~1.3x the straight line.
        if mode == AccessMode.ROAD.value:
            distance = round(distance * 1.3, 3)
        minutes = estimate_travel_minutes(distance, _MODE_SPEED_KMH.get(mode, 40.0))
        return {
            "source": "fallback_estimate",
            "verified": False,
            "distance_km": distance,
            "travel_minutes": minutes,
            "note": (
                "UNVERIFIED ESTIMATE - live routing (OSRM) unavailable. "
                "Distance is a great-circle approximation and travel time assumes a "
                f"{_MODE_SPEED_KMH.get(mode, 40.0):.0f} km/h average speed for {mode} "
                "access. Terrain, flooding and road closures are not accounted for. "
                "Route verification required before operational use."
            ),
        }

    def _no_route(self, reason: str, note: str) -> dict[str, Any]:
        return {
            "source": "unavailable",
            "verified": False,
            "distance_km": None,
            "travel_minutes": None,
            "reason": reason,
            "note": note,
        }


# ---------------------------------------------------------------------------
# Accessibility assessment (combines road records with routing capability)
# ---------------------------------------------------------------------------


def assess_accessibility(
    *,
    roads: list[Any],
    status: RouteStatus | None = None,
    fact_status: FactStatus | None = None,
    blocked_reason: str | None = None,
    route: dict[str, Any] | None = None,
    zone: str | None = None,
) -> dict[str, Any]:
    """Build the accessibility context consumed by the assessment agent.

    Separates three things the UI must never conflate:

    ``known_condition``
        Authoritative report from a road authority / field team.
    ``inferred_observation``
        Agent hypothesis derived from the absence of a usable route.
    ``verification_required``
        Anything that cannot be established from available data.
    """
    known_status: str | None = None
    known_road_id: str | None = None
    known_blocked_reason: str | None = None
    known_fact_status: str | None = None
    for road in roads:
        value = enum_value(field_get(road, "status"))
        if value == RouteStatus.UNKNOWN.value:
            continue
        r_fact = enum_value(field_get(road, "fact_status"))
        if r_fact in {FactStatus.UNVERIFIED.value, FactStatus.INFERRED.value}:
            # An unverified or inferred road record is a hypothesis, not a fact,
            # so it must not be promoted into ``known_condition``.
            continue
        known_status = value
        known_road_id = field_get(road, "road_id")
        known_blocked_reason = field_get(road, "blocked_reason")
        known_fact_status = r_fact or FactStatus.KNOWN.value
        break

    effective = known_status or enum_value(status) or RouteStatus.UNKNOWN.value
    blocked_reason = blocked_reason or known_blocked_reason

    inferred: list[str] = []
    if route and not route.get("verified") and route.get("distance_km") is None:
        inferred.append(
            "No usable route could be computed to this location; the area may be "
            "isolated. This is an INFERENCE, not a confirmed road closure."
        )
    if effective == RouteStatus.UNKNOWN.value:
        inferred.append(
            "No authoritative route condition report is on file for the primary "
            "access route."
        )

    verification_required = False
    notes: list[str] = []
    if effective == RouteStatus.UNKNOWN.value:
        verification_required = True
        notes.append("Route verification required - primary access condition unknown.")
    if blocked_reason:
        notes.append(f"Reported reason: {blocked_reason}")

    return {
        "zone": zone,
        "status": effective,
        "status_label": ACCESSIBILITY_LABELS.get(effective, effective),
        "known_condition": {
            "status": known_status,
            "road_id": known_road_id,
            "is_fact": known_status is not None,
            "fact_status": known_fact_status or enum_value(fact_status),
            "source": (
                "authoritative report"
                if known_status is not None
                else "no authoritative report on file"
            ),
            "note": (
                None
                if known_status is not None
                else "No road authority or field team has reported a condition for "
                "this route. The status below is unknown, not open."
            ),
        },
        "inferred_observation": {
            "hypotheses": inferred,
            "is_fact": False,
            "fact_status": FactStatus.INFERRED.value,
        },
        "inferred_observations": [
            {"observation": text, "is_fact": False, "fact_status": FactStatus.INFERRED.value}
            for text in inferred
        ],
        "verification_required": verification_required,
        "notes": notes,
        "route": route or {},
        "evidence": (
            f"Primary access route to {zone or 'incident'} reported as {effective}."
            if known_status
            else "No confirmed route condition available; treated as unknown."
        ),
    }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tool = RoutingTool()


async def get_route(
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    mode: str = AccessMode.ROAD.value,
) -> dict[str, Any]:
    return await _tool.route(origin, destination, mode)


def routing_status() -> dict[str, Any]:
    return {
        "configured": True,
        "mode": "live" if _tool._live_available is not False else "fallback_estimate",
        "provider": "OSRM (OpenStreetMap)",
        "base_url": _tool.base_url,
    }