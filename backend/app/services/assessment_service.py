"""Situation assessment composition (Agent 2).

Collects the deterministic inputs for one incident - road/accessibility state
and weather - runs the priority model over them, and returns a single
explainable assessment payload.

This module is pure composition: no DB writes, no side effects. The agent layer
calls it and decides what to persist.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.core.config import settings
from app.models.enums import FactStatus, RouteStatus
from app.services.allocation_engine import estimate_requirement
from app.services.priority_calculator import PriorityCalculator
from app.tools import geocode, get_route, get_weather
from app.tools.routing_tool import assess_accessibility

logger = logging.getLogger(__name__)

__all__ = ["AssessmentService"]


class AssessmentService:
    def __init__(self, priority: PriorityCalculator | None = None) -> None:
        self.priority = priority or PriorityCalculator()

    # -- geometry helpers ---------------------------------------------------

    async def ensure_coordinates(
        self, location: str | None, latitude: float | None, longitude: float | None
    ) -> dict[str, Any]:
        """Geocode a location only when coordinates are genuinely absent."""
        if latitude is not None and longitude is not None:
            return {
                "latitude": latitude,
                "longitude": longitude,
                "source": "provided",
                "verified": True,
                "note": None,
            }
        result = await geocode(location)
        return {
            "latitude": result.get("latitude"),
            "longitude": result.get("longitude"),
            "source": result.get("source"),
            "verified": result.get("verified", False),
            "note": result.get("note"),
        }

    async def resolve_coordinates_for_resource(
        self, resource: Any
    ) -> tuple[float | None, float | None]:
        return resource.latitude, resource.longitude

    # -- access -------------------------------------------------------------

    async def build_accessibility(
        self,
        incident: dict[str, Any],
        roads: Sequence[Any],
        *,
        route: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Determine how accessible an incident's location is.

        The result keeps the *known* condition and the *inferred* observation in
        separate keys, per the specification.
        """
        overrides = overrides or {}

        # Roads considered relevant to this incident's zone.
        zone = incident.get("location")
        relevant = [
            r
            for r in roads
            if getattr(r, "destination_zone", None) == zone
            or getattr(r, "origin_zone", None) == zone
        ]
        if not relevant:
            relevant = list(roads)

        if route is None:
            ilat, ilon = incident.get("latitude"), incident.get("longitude")
            if ilat is not None and ilon is not None:
                requirement = estimate_requirement(incident)
                mode = "water" if requirement.is_water_incident else "road"
                route = await get_route((ilat, ilon), None, mode=mode)
            else:
                route = None

        return assess_accessibility(
            roads=relevant,
            status=overrides.get("status"),
            fact_status=overrides.get("fact_status", FactStatus.UNVERIFIED),
            blocked_reason=overrides.get("blocked_reason"),
            route=route,
            zone=zone,
        )

    # -- weather ------------------------------------------------------------

    async def build_weather(
        self,
        incident: dict[str, Any],
        *,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        lat = incident.get("latitude")
        lon = incident.get("longitude")
        weather = await get_weather(lat, lon, scenario=scenario)
        weather["affects_operations"] = bool(
            weather.get("worsening") or weather.get("severe_warning")
        )
        if weather.get("simulation_mode"):
            weather["verification_required"] = True
        return weather

    # -- full assessment ----------------------------------------------------

    async def assess(
        self,
        incident: dict[str, Any],
        roads: Sequence[Any],
        *,
        assigned_resource_count: int = 0,
        plan_exists: bool = False,
        scenario: str | None = None,
        accessibility: dict[str, Any] | None = None,
        weather: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Produce the full, explainable assessment for one incident."""
        if weather is None:
            weather = await self.build_weather(incident, scenario=scenario)
        if accessibility is None:
            accessibility = await self.build_accessibility(incident, roads)

        result = self.priority.calculate(
            incident,
            weather=weather,
            accessibility=accessibility,
            assigned_resource_count=assigned_resource_count,
            plan_exists=plan_exists,
        )

        payload = result.to_factors_payload()
        payload.update(
            {
                "incident_id": incident.get("incident_id"),
                "weather": weather,
                "accessibility": accessibility,
                "route_verification_required": bool(
                    accessibility.get("verification_required")
                ),
                "weather_is_simulated": bool(weather.get("simulation_mode")),
            }
        )
        return payload

    # -- fleet-wide ---------------------------------------------------------

    async def assess_many(
        self,
        incidents: Sequence[dict[str, Any]],
        roads: Sequence[Any],
        assigned_counts: dict[str, int] | None = None,
        plan_incidents: set[str] | None = None,
        scenario: str | None = None,
    ) -> list[dict[str, Any]]:
        assigned_counts = assigned_counts or {}
        plan_incidents = plan_incidents or set()
        results = []
        for incident in incidents:
            iid = str(incident.get("incident_id"))
            results.append(
                await self.assess(
                    incident,
                    roads,
                    assigned_resource_count=assigned_counts.get(iid, 0),
                    plan_exists=iid in plan_incidents,
                    scenario=scenario,
                )
            )
        return results