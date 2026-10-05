"""Agent 5 - Emergency Resource Management Agent.

Owns the resource registry view: what exists, what each unit can do, where it
is, and what it is currently committed to.

It also computes the *capability gap* per incident - the difference between what
the incident type requires and what the fleet can actually supply right now.
That gap is what feeds the shortage detector in Agent 6, and it is computed
from the registry only, never from an assumption that more equipment exists.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.agents.base import BaseAgent
from app.core.constants import (
    REQUIRED_CAPABILITIES,
    RESOURCE_TYPE_ACCESS_MODES,
    RESOURCE_TYPE_CAPABILITIES,
    SUITABLE_RESOURCE_TYPES,
)
from app.core.utils import enum_value
from app.orchestration.state import WorkflowState
from app.services.allocation_engine import estimate_requirement

logger = logging.getLogger(__name__)

__all__ = ["ResourceAgent"]


class ResourceAgent(BaseAgent):
    name = "emergency_resource_management"
    role = "Track resource inventory, capability and availability"
    node_name = "resource_output"

    system_prompt = """
You report the current state of emergency resources. You never create, move,
or promise a resource. If the registry does not contain a unit, it does not
exist for planning purposes.
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resources = state.get("resources") or []
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        assigned = state.get("assigned_counts") or {}

        by_status = Counter(enum_value(r.get("status")) or "unknown" for r in resources)
        by_type = Counter(enum_value(r.get("resource_type")) or "unknown" for r in resources)

        # Which capability tags exist anywhere in the fleet?
        available_tags: Counter[str] = Counter()
        for res in resources:
            if enum_value(res.get("status")) != "available":
                continue
            tags = res.get("capability_tags") or RESOURCE_TYPE_CAPABILITIES.get(
                enum_value(res.get("resource_type")), []
            )
            available_tags.update(enum_value(t) for t in tags)

        unit_capacity: dict[str, int] = {}
        for res in resources:
            if enum_value(res.get("status")) != "available":
                continue
            rtype = enum_value(res.get("resource_type"))
            unit_capacity[rtype] = unit_capacity.get(rtype, 0) + int(res.get("capacity") or 0)

        capability_gaps = [
            self._capability_gap(incident, available_tags, unit_capacity, by_type)
            for incident in incidents
        ]

        unavailable = [
            {
                "resource_id": r.get("resource_id"),
                "resource_type": enum_value(r.get("resource_type")),
                "status": enum_value(r.get("status")),
                "expected_availability": r.get("expected_availability"),
            }
            for r in resources
            if enum_value(r.get("status")) in {"unavailable", "maintenance"}
        ]

        return {
            "resource_output": {
                "total": len(resources),
                "by_status": dict(by_status),
                "by_type": dict(by_type),
                "available_capability_tags": dict(available_tags),
                "available_capacity_by_type": unit_capacity,
                "capability_gaps": capability_gaps,
                "unavailable_units": unavailable,
                "counts": {
                    "available": by_status.get("available", 0),
                    "reserved": by_status.get("reserved", 0),
                    "deployed": by_status.get("deployed", 0),
                    "returning": by_status.get("returning", 0),
                    "unavailable": by_status.get("unavailable", 0),
                    "maintenance": by_status.get("maintenance", 0),
                },
                "assigned_counts": dict(assigned),
                "notes": [
                    "Only units with status 'available' are eligible for new allocation.",
                    "A resource assigned to an incident is excluded from all further "
                    "planning to prevent double allocation.",
                ],
            }
        }

    @staticmethod
    def _capability_gap(
        incident: dict[str, Any],
        available_tags: Counter,
        unit_capacity: dict[str, int],
        by_type: Counter,
    ) -> dict[str, Any]:
        requirement = estimate_requirement(incident)
        satisfied: list[str] = []
        unsatisfied: list[str] = []
        for capability in requirement.required_capabilities:
            (satisfied if available_tags.get(capability, 0) > 0 else unsatisfied).append(capability)

        suitable_available = sum(
            by_type.get(t, 0) for t in requirement.suitable_types
        )
        capacity = sum(
            unit_capacity.get(t, 0) for t in requirement.suitable_types
        )
        people = int(incident.get("people_reported_affected") or 0)

        return {
            "incident_id": requirement.incident_id,
            "required_capabilities": requirement.required_capabilities,
            "satisfied_capabilities": satisfied,
            "unsatisfied_capabilities": unsatisfied,
            "suitable_units_available": suitable_available,
            "available_nominal_capacity": capacity,
            "people_reported_affected": people,
            "nominal_capacity_gap": max(0, people - capacity),
            "has_capability": not unsatisfied,
            "note": (
                "Nominal capacity is a planning figure. It does not establish "
                "operational suitability for this incident."
            ),
        }