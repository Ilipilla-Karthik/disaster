"""Agent 7 - Emergency Coordination & Action Agent.

Consolidates everything the other agents produced into a single coherent
response plan:

* merges duplicate reports into one canonical entry,
* orders recommended actions by the priority score,
* collects unresolved issues and the assumptions the plan relies on,
* proposes the actions a coordinator would need to authorise,
* prepares the action-tracking records and the situation report.

The plan status is always ``awaiting_approval``. Nothing here moves a resource.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.core.utils import enum_value
from app.orchestration.state import WorkflowState
from app.services.shelter_service import ShelterService

logger = logging.getLogger(__name__)

__all__ = ["CoordinationAgent"]


class CoordinationAgent(BaseAgent):
    name = "emergency_coordination_action"
    role = "Consolidate agent outputs into a coordinated response plan"
    node_name = "coordination_output"

    system_prompt = """
You consolidate emergency agent outputs into a coordinated plan for human
approval. Never dispatch, never authorise, never override a commander. State
assumptions and unresolved questions explicitly.
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        assessments = state.get("assessments") or []
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        allocation = state.get("allocation_output") or {}
        weather = state.get("weather_output") or {}
        geo = state.get("geospatial_output") or {}
        intake = state.get("intake_output") or {}
        resource_out = state.get("resource_output") or {}
        trigger = state.get("trigger", "initial_assessment")

        # ---- consolidate incidents (duplicates collapsed, not deleted) ----
        canonical, merged = self._consolidate(incidents)

        assessment_by_id = {str(a.get("incident_id")): a for a in assessments}
        allocations = allocation.get("allocations") or []
        alloc_by_incident: dict[str, list[dict[str, Any]]] = {}
        for a in allocations:
            alloc_by_incident.setdefault(str(a.get("incident_id")), []).append(a)

        # ---- assemble the plan ------------------------------------------
        priority_order = sorted(
            canonical.values(),
            key=lambda i: float(
                (assessment_by_id.get(str(i.get("incident_id")), {}) or {}).get("score") or 0.0
            ),
            reverse=True,
        )

        incidents_in_plan = [str(i.get("incident_id")) for i in priority_order]

        # ---- unresolved issues and assumptions --------------------------
        unresolved: list[str] = list(state.get("unresolved_issues") or [])
        assumptions: list[str] = []

        unresolved.extend(geo.get("unresolved") or [])
        unresolved.extend(intake.get("notes") or [])

        if weather.get("simulation_mode"):
            assumptions.append(
                "Weather figures are SIMULATED, not observed. Priority scores that "
                "depend on weather must be revisited once live data is available."
            )
        if any(
            (a.get("accessibility") or {}).get("verification_required")
            for a in assessments
        ):
            assumptions.append(
                "At least one incident's access route condition is unconfirmed; "
                "deployment feasibility is unverified."
            )
        if assessments and all(
            (a.get("weather") or {}).get("simulation_mode") for a in assessments
        ):
            assumptions.append(
                "Weather-driven deterioration risk is indicative only (simulated data)."
            )
        assumptions.append(
            "Reported affected-population figures are self-reported and unverified; "
            "they are a planning floor, not a confirmed count."
        )
        assumptions.append(
            "Nominal resource capacity does not establish operational suitability; "
            "each recommendation requires operator and commander confirmation."
        )

        # ---- shelter situation ------------------------------------------
        shelters = state.get("shelters") or []
        shelter_summary = self._shelter_summary(shelters)

        # ---- recommended actions (proposed, not executed) ---------------
        actions: list[dict[str, Any]] = []
        for incident in priority_order:
            iid = str(incident.get("incident_id"))
            assessment = assessment_by_id.get(iid) or {}
            for alloc in alloc_by_incident.get(iid, []):
                actions.append(
                    {
                        "action_id": f"ACT-{uuid.uuid4().hex[:8].upper()}",
                        "incident_id": iid,
                        "resource_id": alloc.get("resource_id"),
                        "plan_id": None,
                        "action_type": "deploy_resource",
                        "description": (
                            f"Deploy {alloc.get('resource_id')} "
                            f"({alloc.get('resource_type')}) to {iid}"
                        ),
                        "priority": str(
                            assessment.get("severity") or "verification_required"
                        ),
                        "status": "pending",
                        "assigned_to": None,
                        "start_time": None,
                        "completion_time": None,
                        "remarks": alloc.get("rationale"),
                        "requires_approval": True,
                        "constraints": alloc.get("constraints") or [],
                    }
                )

        # Non-deployment actions derived from the situational picture.
        for shelter in shelter_summary.get("near_capacity", []):
            actions.append(
                {
                    "action_id": f"ACT-{uuid.uuid4().hex[:8].upper()}",
                    "incident_id": None,
                    "resource_id": None,
                    "action_type": "review_shelter_capacity",
                    "description": (
                        f"Review shelter {shelter['shelter_id']} at "
                        f"{shelter['utilization_pct']}% capacity "
                        f"({shelter['available_capacity']} places remaining)"
                    ),
                    "priority": "high",
                    "status": "pending",
                    "assigned_to": None,
                    "remarks": "Capacity threshold alert requires coordinator review.",
                    "requires_approval": True,
                }
            )

        if any(
            (a.get("accessibility") or {}).get("status") in {"closed", "impassable"}
            for a in assessments
        ):
            actions.append(
                {
                    "action_id": f"ACT-{uuid.uuid4().hex[:8].upper()}",
                    "incident_id": None,
                    "resource_id": None,
                    "action_type": "verify_access_route",
                    "description": (
                        "Field-verify the closed/partially blocked access route(s) and "
                        "confirm whether an alternative route exists"
                    ),
                    "priority": "high",
                    "status": "pending",
                    "remarks": (
                        "The system will not propose an alternative route; a human "
                        "must confirm what is actually passable."
                    ),
                    "requires_approval": True,
                }
            )

        for shortage in allocation.get("shortages") or []:
            actions.append(
                {
                    "action_id": f"ACT-{uuid.uuid4().hex[:8].upper()}",
                    "incident_id": shortage.get("incident_id"),
                    "resource_id": None,
                    "action_type": "escalate_resource_shortage",
                    "description": (
                        f"Escalate shortage: {shortage.get('label')} - deficit "
                        f"{shortage.get('deficit')} {shortage.get('unit', 'units')}"
                    ),
                    "priority": "high",
                    "status": "pending",
                    "remarks": shortage.get("note"),
                    "requires_approval": True,
                }
            )

        # ---- plan summary ------------------------------------------------
        plan_summary = self._plan_summary(
            trigger, priority_order, assessment_by_id, allocations, allocation, shelter_summary
        )

        return {
            "actions": actions,
            "unresolved_issues": _dedupe(unresolved),
            "assumptions": _dedupe(assumptions),
            "coordination_output": {
                "title": (
                    f"{'Replan' if trigger != 'initial_assessment' else 'Response'} "
                    f"- Example District flood, {len(incidents_in_plan)} incident(s)"
                ),
                "summary": plan_summary,
                "status": "awaiting_approval",
                "trigger": trigger,
                "incident_ids": incidents_in_plan,
                "canonical_incidents": [i.get("incident_id") for i in priority_order],
                "merged_duplicates": merged,
                "allocations": allocations,
                "shortages": allocation.get("shortages") or [],
                "alternatives": allocation.get("alternatives") or [],
                "proposed_actions": actions,
                "shelter_summary": shelter_summary,
                "weather_snapshot": weather.get("snapshot"),
                "resource_summary": resource_out.get("counts"),
                "assumptions": _dedupe(assumptions),
                "unresolved_issues": _dedupe(unresolved),
                "requires_approval": True,
                "disclaimer": (
                    "Awaiting authorised human approval. No resource has been dispatched."
                ),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _consolidate(
        incidents: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Collapse duplicate reports under their canonical incident.

        Duplicates are grouped, never removed - the grouped ids stay visible so a
        coordinator can review and un-merge if the reports were genuinely distinct.
        """
        canonical: dict[str, dict[str, Any]] = {}
        merged: list[dict[str, Any]] = []

        for incident in incidents:
            iid = str(incident.get("incident_id"))
            parent = incident.get("duplicate_of")
            if incident.get("is_duplicate") and parent:
                if parent in canonical:
                    linked = canonical[parent].setdefault("linked_incident_ids", [])
                    if iid not in linked:
                        linked.append(iid)
                merged.append(
                    {
                        "duplicate_id": iid,
                        "canonical_id": parent,
                        "similarity": incident.get("duplicate_similarity"),
                        "action": "grouped_for_review",
                    }
                )
                continue
            canonical[iid] = dict(incident)
            canonical[iid].setdefault("linked_incident_ids", [])

        return canonical, merged

    @staticmethod
    def _shelter_summary(shelters: list[dict[str, Any]]) -> dict[str, Any]:
        capacity = sum(int(s.get("capacity") or 0) for s in shelters)
        occupancy = sum(int(s.get("current_occupancy") or 0) for s in shelters)
        near = [
            {
                "shelter_id": s.get("shelter_id"),
                "name": s.get("name"),
                "utilization_pct": round(
                    (s.get("current_occupancy") or 0) / s["capacity"] * 100, 2
                )
                if s.get("capacity")
                else 0.0,
                "available_capacity": max(
                    0, (s.get("capacity") or 0) - (s.get("current_occupancy") or 0)
                ),
            }
            for s in shelters
            if s.get("capacity")
            and (s.get("current_occupancy") or 0) / s["capacity"] * 100 >= 85
        ]
        return {
            "total": len(shelters),
            "capacity": capacity,
            "occupancy": occupancy,
            "available_capacity": max(0, capacity - occupancy),
            "near_capacity": near,
            "formula": "available_capacity = max(0, capacity - current_occupancy)",
        }

    @staticmethod
    def _plan_summary(
        trigger: str,
        ordered: list[dict[str, Any]],
        assessment_by_id: dict[str, Any],
        allocations: list[dict[str, Any]],
        allocation: dict[str, Any],
        shelter_summary: dict[str, Any],
    ) -> str:
        if not ordered:
            return "No incidents require a response plan at this time."

        top = ordered[0]
        top_assessment = assessment_by_id.get(str(top.get("incident_id")), {})
        parts = [
            f"Plan generated by trigger '{trigger}' covering "
            f"{len(ordered)} active incident(s). "
            f"Highest priority: {top.get('incident_id')} in {top.get('location')} "
            f"(score {top_assessment.get('score')}, "
            f"{top_assessment.get('severity')})."
        ]
        if allocations:
            parts.append(
                f"{len(allocations)} resource allocation(s) proposed and awaiting "
                "commander approval."
            )
        if allocation.get("shortages"):
            parts.append(
                f"{len(allocation['shortages'])} resource shortage(s) requiring escalation."
            )
        parts.append(
            f"Shelter capacity remaining across {shelter_summary.get('total', 0)} "
            f"shelter(s): {shelter_summary.get('available_capacity', 0)} places."
        )
        return " ".join(parts)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out