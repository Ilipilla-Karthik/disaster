"""Resource allocation & response planning engine (Requirements 8, 11, Agent 6).

Purely deterministic. Given the same operational picture it always produces the
same plan, which is a hard requirement for an emergency tool that must be
auditable and reproducible.

Hard constraints enforced
-------------------------
1. **No double allocation.** A resource already assigned to an incident is
   excluded outright, so the same unit can never appear in two allocations.
2. **Capability must match.** A resource lacking the incident's required
   capability tags is excluded (a road vehicle is not sent to a water rescue).
3. **Physical access mode must be compatible.** Boats are not routed over roads.
4. **Nothing is invented.** If no suitable resource exists the engine reports a
   shortage and names the deficit. It never assumes a helicopter, an extra boat
   or an alternative road.
5. **Nominal capacity is not operational suitability.** Capacity is recorded as
   a contributing factor, but every recommendation is explicitly gated on
   commander approval and on confirmation that conditions permit deployment.

Scoring
-------
    match_score = 0.40 * capability_fit
                + 0.25 * proximity_fit
                + 0.15 * availability
                + 0.10 * capacity_fit
                + 0.10 * access_fit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.core.constants import (
    ACCESS_MODE_BY_INCIDENT_TYPE,
    ALLOCATION_WEIGHTS,
    MAX_DEPLOYMENT_RADIUS_KM,
    REQUIRED_CAPABILITIES,
    RESOURCE_TYPE_ACCESS_MODES,
    RESOURCE_TYPE_CAPABILITIES,
    SUITABLE_RESOURCE_TYPES,
)
from app.core.utils import enum_value as _enum_value
from app.core.utils import field_get as _field_get
from app.models.enums import (
    AccessMode,
    ResourceStatus,
    ResourceType,
    RouteStatus,
)
from app.services.geo import haversine_km

logger = logging.getLogger(__name__)

__all__ = [
    "AllocationEngine",
    "AllocationCandidate",
    "ResourceRequirement",
    "estimate_requirement",
]


@dataclass
class ResourceRequirement:
    """What an incident needs, derived from its type and affected population."""

    incident_id: str
    incident_type: str
    people: int
    required_capabilities: list[str]
    suitable_types: list[str]
    access_modes: list[str]

    @property
    def is_water_incident(self) -> bool:
        return AccessMode.WATER.value in self.access_modes


@dataclass
class AllocationCandidate:
    """A scored (resource -> incident) pairing with its justification."""

    resource: Any
    incident_id: str
    match_score: float
    capability_fit: float
    proximity_fit: float
    availability: float
    capacity_fit: float
    access_fit: float
    distance_km: float | None
    travel_minutes: float | None
    route_status: str | None
    route_verified: bool
    rationale: str
    constraints: list[str] = field(default_factory=list)
    match_factors: dict[str, Any] = field(default_factory=dict)

    coverage_gap: int | None = None

    def to_dict(self) -> dict[str, Any]:
        res = self.resource
        return {
            "incident_id": self.incident_id,
            "resource_id": _get(res, "resource_id"),
            "resource_type": _value(_get(res, "resource_type")),
            "resource_label": _get(res, "location"),
            "rationale": self.rationale,
            "match_factors": self.match_factors,
            "distance_km": self.distance_km,
            "travel_minutes": self.travel_minutes,
            "route_status": self.route_status,
            "route_verified": self.route_verified,
            "coverage_gap": self.coverage_gap,
            "capacity_contribution": int(_get(res, "capacity", 0) or 0),
            "requires_commander_approval": True,
            "constraints": self.constraints,
        }


def estimate_requirement(incident: dict[str, Any]) -> ResourceRequirement:
    """Derive the requirement for one incident from its hazard type and size."""
    itype = _value(incident.get("incident_type") or "other").lower()
    people = int(incident.get("people_reported_affected") or 0)
    return ResourceRequirement(
        incident_id=str(incident.get("incident_id") or ""),
        incident_type=itype,
        people=people,
        required_capabilities=list(REQUIRED_CAPABILITIES.get(itype, [])),
        suitable_types=list(SUITABLE_RESOURCE_TYPES.get(itype, [])),
        access_modes=list(ACCESS_MODE_BY_INCIDENT_TYPE.get(itype, [AccessMode.ROAD.value])),
    )


class AllocationEngine:
    """Scores and selects resource allocations under hard constraints."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or ALLOCATION_WEIGHTS

    # -- public API ---------------------------------------------------------

    def select(
        self,
        incidents: Sequence[dict[str, Any]],
        resources: Iterable[Any],
        *,
        assigned_resource_ids: set[str] | None = None,
        routes: dict[str, dict[str, Any]] | None = None,
        access_by_incident: dict[str, dict[str, Any]] | None = None,
        max_per_incident: int | None = None,
    ) -> tuple[list[AllocationCandidate], list[dict[str, Any]]]:
        """Greedy priority-ordered selection.

        Returns ``(selected, shortages)``. Incidents are served in descending
        priority order so scarce capability goes to the highest-priority
        incident first - but *capability and access* gate eligibility before
        priority ever influences the choice.
        """
        resources = list(resources)
        routes = routes or {}
        access_by_incident = access_by_incident or {}
        taken: set[str] = set(assigned_resource_ids or set())

        ordered = sorted(
            incidents,
            key=lambda i: float(i.get("priority_score") or 0.0),
            reverse=True,
        )

        selected: list[AllocationCandidate] = []
        shortages: list[dict[str, Any]] = []

        for incident in ordered:
            requirement = estimate_requirement(incident)
            accessibility = access_by_incident.get(requirement.incident_id) or {}
            if not requirement.suitable_types:
                continue

            candidates = self.rank_candidates(
                incident, requirement, resources, taken, routes, accessibility
            )

            usable = [c for c in candidates if c.match_score > 0]
            need = _target_asset_count(requirement.people)
            chosen = usable[:need] if max_per_incident is None else usable[:max_per_incident]

            if not chosen:
                shortages.append(
                    self._shortage(
                        requirement,
                        candidates,
                        reason="no_suitable_resource",
                    )
                )
                continue

            # How many people can the selected assets actually move?
            covered = sum(int(_get(c.resource, "capacity", 0) or 0) for c in chosen)
            gap = max(0, requirement.people - covered)
            for cand in chosen:
                cand.constraints.append(
                    f"Nominal capacity {_get(cand.resource, 'capacity', 0)} "
                    f"(combined {covered} of {requirement.people} people reported). "
                    "Capacity is a planning figure only - operational suitability "
                    "must be confirmed by the commander."
                )
                if gap:
                    cand.coverage_gap = gap
                taken.add(str(_get(cand.resource, "resource_id", "")))
                selected.append(cand)

            if gap:
                shortages.append(
                    {
                        "resource_type": _primary_type(requirement),
                        "label": (
                            f"Evacuation/rescue capacity for {requirement.incident_id}"
                        ),
                        "required": requirement.people,
                        "available": covered,
                        "deficit": gap,
                        "unit": "person-places",
                        "incident_id": requirement.incident_id,
                        "note": (
                            f"Suitable assets cover {covered} of {requirement.people} "
                            f"reported people for {requirement.incident_id}. Deficit of "
                            f"{gap} person-places. Escalation or additional mutual-aid "
                            "request required. Additional assets are NOT assumed to exist."
                        ),
                    }
                )

        return selected, shortages

    # -- ranking ------------------------------------------------------------

    def rank_candidates(
        self,
        incident: dict[str, Any],
        requirement: ResourceRequirement,
        resources: Sequence[Any],
        taken: set[str],
        routes: dict[str, dict[str, Any]],
        accessibility: dict[str, Any] | None = None,
    ) -> list[AllocationCandidate]:
        """Score every eligible resource. Ineligible resources are dropped."""
        ilat, ilon = incident.get("latitude"), incident.get("longitude")
        access_status = (accessibility or {}).get("status", RouteStatus.UNKNOWN.value)

        out: list[AllocationCandidate] = []
        for res in resources:
            rid = str(_get(res, "resource_id", ""))
            rtype = _value(_get(res, "resource_type", ""))

            # -- hard constraint 1: no double allocation -------------------
            if rid in taken:
                continue

            # -- hard constraint 2: resource must be idle --------------------
            if _value(_get(res, "status", "")) != ResourceStatus.AVAILABLE.value:
                continue
            if _get(res, "current_assignment"):
                continue

            # -- hard constraint 3: type suitability ------------------------
            if rtype not in requirement.suitable_types:
                continue

            # -- hard constraint 4: capability tags -------------------------
            tags = set(_get(res, "capability_tags", None) or [])
            if not tags:
                tags = set(RESOURCE_TYPE_CAPABILITIES.get(rtype, []))
            missing_caps = [c for c in requirement.required_capabilities if c not in tags]
            if missing_caps:
                continue

            # -- hard constraint 5: physical access mode --------------------
            modes = set(_get(res, "access_modes", None) or
                        RESOURCE_TYPE_ACCESS_MODES.get(rtype, [AccessMode.ROAD.value]))
            modes = {_value(m) for m in modes}
            if not (modes & set(requirement.access_modes)):
                continue

            res_lat, res_lon = _get(res, "latitude"), _get(res, "longitude")
            distance = (
                haversine_km(float(ilat), float(ilon), float(res_lat), float(res_lon))
                if (ilat is not None and ilon is not None and res_lat is not None and res_lon is not None)
                else None
            )

            max_radius = MAX_DEPLOYMENT_RADIUS_KM.get(rtype, 80.0)
            constraints: list[str] = []
            if distance is not None and distance > max_radius:
                constraints.append(
                    f"Exceeds {rtype} maximum useful deployment radius "
                    f"({distance:.1f}km > {max_radius:.0f}km)."
                )
                proximity_fit = 0.0
            else:
                proximity_fit = _proximity_fit(distance, max_radius)

            route = routes.get(rid) or {}
            travel = route.get("travel_minutes")
            route_verified = bool(route.get("verified"))
            route_status = _route_status_for(access_status, route)

            # A closed route is not a reason to recommend a deployment we
            # cannot justify, but it IS a reason to flag it.
            if route_status == RouteStatus.CLOSED.value and requirement.is_water_incident:
                constraints.append(
                    "Land route to this incident is CLOSED; water access may be "
                    "required. Confirm an accessible water route before deployment."
                )

            capability_fit = 1.0 if not requirement.required_capabilities else (
                len([c for c in requirement.required_capabilities if c in tags])
                / len(requirement.required_capabilities)
            )
            capacity = int(_get(res, "capacity", 0) or 0)
            capacity_fit = (
                min(1.0, capacity / max(1, requirement.people)) if requirement.people else 1.0
            )
            access_fit = 1.0 if route_verified else (0.5 if route_status != RouteStatus.CLOSED.value else 0.2)
            availability = 1.0

            score = (
                self.weights["capability"] * capability_fit
                + self.weights["distance"] * proximity_fit
                + self.weights["availability"] * availability
                + self.weights["capacity"] * capacity_fit
                + self.weights["access"] * access_fit
            ) / 100.0

            if route_verified:
                route_note = f"{travel} min via verified route"
            elif travel is not None:
                route_note = f"~{travel} min UNVERIFIED estimate"
            else:
                route_note = "route verification required"

            rationale = (
                f"{rtype.replace('_', ' ').title()} {rid} at "
                f"{_get(res, 'location', 'unknown')} - capability "
                f"{'match' if capability_fit == 1.0 else 'partial'} "
                f"(needs {', '.join(requirement.required_capabilities) or 'none'}), "
                f"{distance:.1f}km away, {route_note}. "
                f"Recommended subject to emergency commander approval and "
                f"confirmation that conditions permit deployment."
                if distance is not None
                else f"{rtype.replace('_', ' ').title()} {rid} - incident coordinates "
                f"unknown, so distance could not be computed; "
                f"route verification required."
            )

            candidate = AllocationCandidate(
                resource=res,
                incident_id=requirement.incident_id,
                match_score=round(score, 4),
                capability_fit=round(capability_fit, 4),
                proximity_fit=round(proximity_fit, 4),
                availability=availability,
                capacity_fit=round(capacity_fit, 4),
                access_fit=round(access_fit, 4),
                distance_km=round(distance, 3) if distance is not None else None,
                travel_minutes=travel,
                route_status=route_status,
                route_verified=route_verified,
                rationale=rationale,
                constraints=constraints,
                match_factors={
                    "capability_fit": capability_fit,
                    "proximity_fit": proximity_fit,
                    "availability": availability,
                    "capacity_fit": capacity_fit,
                    "access_fit": access_fit,
                    "weights": self.weights,
                    "route_source": route.get("source"),
                    "incident_access_status": access_status,
                },
            )
            out.append(candidate)

        out.sort(key=lambda c: c.match_score, reverse=True)
        return out

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _shortage(
        requirement: ResourceRequirement,
        candidates: Sequence[AllocationCandidate],
        reason: str,
    ) -> dict[str, Any]:
        if candidates:
            eligible_ids = [str(_get(c.resource, "resource_id", "")) for c in candidates]
            note = (
                f"All currently suitable assets for {requirement.incident_id} are "
                f"already allocated ({', '.join(eligible_ids)}). No additional units "
                "are assumed to exist."
            )
            primary = _primary_type(requirement)
            available = 0
        else:
            note = (
                f"No resource in the registry satisfies the capability, access-mode "
                f"and status requirements for {requirement.incident_id} "
                f"(needs: {', '.join(requirement.required_capabilities) or 'none'}; "
                f"suitable types: {', '.join(requirement.suitable_types)}). "
                "Mutual-aid escalation or capability acquisition required - the "
                "system will not assume unlisted assets exist."
            )
            primary = _primary_type(requirement)
            available = 0

        return {
            "resource_type": primary,
            "label": f"Response capability for {requirement.incident_id}",
            "required": max(1, requirement.people // 20 or 1),
            "available": available,
            "deficit": max(1, requirement.people // 20 or 1) - available,
            "unit": "units",
            "incident_id": requirement.incident_id,
            "reason": reason,
            "note": note,
        }


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _proximity_fit(distance: float | None, max_radius: float) -> float:
    if distance is None:
        return 0.0
    if distance >= max_radius:
        return 0.0
    return max(0.0, 1.0 - (distance / max_radius))


def _target_asset_count(people: int) -> int:
    """How many assets an incident plausibly needs.

    Derived from reported population and typical per-asset capacity, and
    deliberately capped at 3 - beyond that the coordinator should be splitting
    the task, not sending a convoy automatically.
    """
    if people <= 0:
        return 1
    if people <= 15:
        return 1
    if people <= 50:
        return 2
    return 3


def _primary_type(requirement: ResourceRequirement) -> str:
    return requirement.suitable_types[0] if requirement.suitable_types else ResourceType.OTHER.value


def _route_status_for(access_status: str, route: dict[str, Any]) -> str:
    if access_status in {RouteStatus.CLOSED.value, RouteStatus.IMPASSABLE.value}:
        return RouteStatus.CLOSED.value
    if route.get("verified"):
        return RouteStatus.OPEN.value
    return RouteStatus.UNKNOWN.value


def _value(value: Any) -> str:
    return _enum_value(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return _field_get(obj, key, default)