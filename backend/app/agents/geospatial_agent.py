"""Agent 4 - Geospatial & Accessibility Agent.

Resolves locations, computes routes, measures proximity to resources and
shelters, and - critically - keeps **known road closures** and **AI-inferred
accessibility problems** strictly separate.

The distinction is structural, not cosmetic:

* A road condition reported by an authority lives in the ``Roads`` table with
  ``fact_status = KNOWN``. It is a fact.
* Anything this agent infers ("no route could be computed, so the area may be
  isolated") lands in ``inferred_observation`` with ``is_fact = False`` and
  ``fact_status = INFERRED``. It is a hypothesis requiring field verification.

When no routing data is available the agent says "Route verification required"
and produces no distance, rather than fabricating an alternative route.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.core.constants import RESOURCE_TYPE_ACCESS_MODES
from app.core.utils import enum_value, field_get
from app.models.enums import AccessMode, FactStatus, RouteStatus
from app.orchestration.state import WorkflowState
from app.services.allocation_engine import estimate_requirement
from app.services.geo import haversine_km
from app.tools import get_route
from app.tools.routing_tool import assess_accessibility

logger = logging.getLogger(__name__)

__all__ = ["GeospatialAgent"]


class GeospatialAgent(BaseAgent):
    name = "geospatial_accessibility"
    role = "Geocode locations, analyse routes and accessibility"
    node_name = "geospatial_output"

    system_prompt = """
You interpret geospatial conditions for emergency access. You must never claim
a road is open or closed unless an authoritative report says so, and you must
never invent an alternative route. When data is missing, answer
"Verification Required".
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        roads = state.get("roads") or []
        resources = state.get("resources") or []
        shelters = state.get("shelters") or []

        access_by_incident: dict[str, Any] = {}
        routes_to_resources: dict[str, Any] = {}
        proximity: dict[str, Any] = {}
        unresolved: list[str] = []
        notes: list[str] = []

        for incident in incidents:
            iid = str(incident.get("incident_id"))
            requirement = estimate_requirement(incident)
            ilat, ilon = incident.get("latitude"), incident.get("longitude")

            access = await self._assess_access(incident, roads, requirement)
            access_by_incident[iid] = access

            if not (ilat is not None and ilon is not None):
                unresolved.append(
                    f"{iid}: no coordinates - map position, distance and routing "
                    "cannot be computed (verification required)."
                )
                continue

            # Nearest resources and shelters by straight-line distance.
            proximity[iid] = {
                "nearest_resources": _nearest(ilat, ilon, resources, "resource_id"),
                "nearest_shelters": _nearest(ilat, ilon, shelters, "shelter_id"),
            }

            # Routes from every candidate resource to this incident.
            mode = AccessMode.WATER.value if requirement.is_water_incident else AccessMode.ROAD.value
            for res in resources:
                rid = str(field_get(res, "resource_id", ""))
                res_modes = {
                    enum_value(m)
                    for m in (
                        field_get(res, "access_modes", None)
                        or RESOURCE_TYPE_ACCESS_MODES.get(
                            enum_value(field_get(res, "resource_type", "")), [AccessMode.ROAD.value]
                        )
                    )
                }
                if mode not in res_modes:
                    # Physically incompatible (e.g. boat to a land incident).
                    routes_to_resources[rid] = {
                        "source": "not_applicable",
                        "verified": False,
                        "distance_km": None,
                        "travel_minutes": None,
                        "note": (
                            f"Resource {rid} operates by {sorted(res_modes)} and cannot "
                            f"reach this incident which requires {mode} access."
                        ),
                    }
                    continue
                routes_to_resources[rid] = await get_route(
                    (field_get(res, "latitude"), field_get(res, "longitude")),
                    (ilat, ilon),
                    mode=mode,
                )

            if access.get("verification_required"):
                notes.append(
                    f"{iid}: access route verification required "
                    f"({access.get('status_label')})."
                )
                unresolved.append(
                    f"{iid}: primary access route condition unconfirmed."
                )

        return {
            "geospatial_output": {
                "access_by_incident": access_by_incident,
                "routes_to_resources": routes_to_resources,
                "proximity": proximity,
                "shelter_proximity": {
                    iid: p.get("nearest_shelters", []) for iid, p in proximity.items()
                },
                "resource_proximity": {
                    iid: p.get("nearest_resources", []) for iid, p in proximity.items()
                },
                "unresolved": unresolved,
                "notes": notes,
                "provenance_note": (
                    "Known road closures (fact_status=known) are authoritative reports. "
                    "Inferred accessibility problems (fact_status=inferred) are agent "
                    "hypotheses and are shown separately; they are never treated as "
                    "confirmed closures."
                ),
            }
        }

    # -- access assessment --------------------------------------------------

    async def _assess_access(
        self,
        incident: dict[str, Any],
        roads: list[dict[str, Any]],
        requirement: Any,
    ) -> dict[str, Any]:
        """Combine road records with a routing attempt for one incident."""
        zone = incident.get("location")
        ilat, ilon = incident.get("latitude"), incident.get("longitude")

        # Roads with an authoritative report for this zone take precedence.
        authoritative = [
            r
            for r in roads
            if (
                field_get(r, "destination_zone") == zone or field_get(r, "origin_zone") == zone
            )
            and enum_value(field_get(r, "fact_status")) in {FactStatus.KNOWN.value}
        ]
        candidates = authoritative or [
            r for r in roads
            if field_get(r, "destination_zone") == zone or field_get(r, "origin_zone") == zone
        ]

        route: dict[str, Any] | None = None
        if ilat is not None and ilon is not None:
            mode = AccessMode.WATER.value if requirement.is_water_incident else AccessMode.ROAD.value
            # Route from the nearest staging point towards the incident.
            origin = _origin_for_zone(roads, zone)
            route = await get_route(origin, (ilat, ilon), mode=mode)

        result = assess_accessibility(
            roads=candidates,
            route=route,
            zone=zone,
        )

        # If every authoritative candidate is closed and we have no alternate
        # open route, surface the access-mode implication explicitly.
        if result["status"] in {RouteStatus.CLOSED.value, RouteStatus.IMPASSABLE.value}:
            if requirement.is_water_incident:
                result["access_mode_implication"] = (
                    "Land access is closed; water access required. No water route to "
                    "this incident is on file - water route verification required."
                )
            else:
                result["access_mode_implication"] = (
                    "Primary road closed. No alternative route is on file. The system "
                    "will not invent one - alternative route verification required."
                )
        else:
            result["access_mode_implication"] = None

        return result


def _origin_for_zone(roads: list[dict[str, Any]], zone: str | None) -> tuple[float, float] | None:
    """Pick a plausible staging origin for a route query towards ``zone``."""
    for road in roads:
        if road.get("destination_zone") == zone and road.get("latitude") is not None:
            return (float(road["latitude"]), float(road["longitude"]))
    for road in roads:
        if road.get("latitude") is not None:
            return (float(road["latitude"]), float(road["longitude"]))
    return None


def _nearest(
    lat: float,
    lon: float,
    items: list[dict[str, Any]],
    key: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Sort items by straight-line distance (not a route)."""
    scored = []
    for item in items:
        if item.get("latitude") is None or item.get("longitude") is None:
            continue
        dist = haversine_km(
            lat, lon, float(item["latitude"]), float(item["longitude"])
        )
        scored.append(
            {
                key: item.get(key),
                "name": item.get("location") or item.get("name"),
                "distance_km": round(dist, 2),
                "straight_line_only": True,
                "note": "Straight-line distance, not a road route.",
            }
        )
    return sorted(scored, key=lambda x: x["distance_km"])[:limit]