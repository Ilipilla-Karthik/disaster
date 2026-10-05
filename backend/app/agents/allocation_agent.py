"""Agent 6 - Resource Allocation & Response Planning Agent.

Turns prioritised incidents plus the resource registry into a set of
*recommendations* - never an action. Every recommendation is gated on human
approval and comes with its reasoning, its route evidence, and any constraint
that could stop it.

Responsibilities:

* match capability to requirement (``app.services.allocation_engine``),
* apply every hard constraint (no double allocation, access-mode compatibility,
  deployment radius),
* identify shortages and name the deficit explicitly,
* propose alternatives that use only resources that actually exist.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.core.utils import enum_value
from app.orchestration.state import WorkflowState
from app.services.allocation_engine import AllocationEngine
from app.tools.llm_tool import get_llm

logger = logging.getLogger(__name__)

__all__ = ["ResourceAllocationAgent"]


class ResourceAllocationAgent(BaseAgent):
    name = "resource_allocation_planning"
    role = "Recommend feasible resource allocations and identify shortages"
    node_name = "allocation_output"
    uses_llm = True

    system_prompt = """
You are advising on emergency resource allocation for a flood response.

You will be given the already-computed deterministic allocation proposals and
the shortages that were detected. Explain them to the coordinator.

Rules you must not break:
- Never recommend a resource that is not in the supplied inventory.
- Never invent helicopters, boats, medical teams or alternative roads.
- Every allocation requires emergency commander approval before dispatch.
- Nominal capacity does not establish operational suitability; say so.
- If there is a shortage, state the exact deficit and that escalation is needed.

Keep the explanation under 150 words.
""".strip()

    def __init__(self) -> None:
        self.engine = AllocationEngine()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        assessments = state.get("assessments") or []
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        resources = state.get("resources") or []
        geo = state.get("geospatial_output") or {}
        routes = geo.get("routes_to_resources") or {}
        access_by_incident = geo.get("access_by_incident") or {}
        assigned_ids = {
            str(r.get("resource_id"))
            for r in resources
            if r.get("current_assignment")
        }

        # Incidents sorted by the deterministic priority score.
        scored: list[dict[str, Any]] = []
        for incident in incidents:
            iid = str(incident.get("incident_id"))
            match = next(
                (a for a in assessments if str(a.get("incident_id")) == iid), None
            )
            enriched = dict(incident)
            enriched["priority_score"] = (match or {}).get("score", 0.0)
            enriched["severity_level"] = (match or {}).get("severity")
            # Skip incidents the reviewer has not cleared for planning.
            if enriched.get("is_duplicate"):
                continue
            scored.append(enriched)

        selected, shortages = self.engine.select(
            scored,
            resources,
            assigned_resource_ids=assigned_ids,
            routes=routes,
            access_by_incident=access_by_incident,
        )

        allocations = [c.to_dict() for c in selected]
        # Re-apply the coverage gap computed during selection.
        for cand, alloc in zip(selected, allocations):
            if cand.coverage_gap:
                alloc["coverage_gap"] = cand.coverage_gap

        alternatives = self._alternatives(shortages, allocations, resources)
        summary = await self._summarise(allocations, shortages, alternatives)

        return {
            "allocations": allocations,
            "shortages": shortages,
            "alternatives": alternatives,
            "allocation_output": {
                "allocations": allocations,
                "shortages": shortages,
                "alternatives": alternatives,
                "allocation_count": len(allocations),
                "shortage_count": len(shortages),
                "incidents_considered": [i.get("incident_id") for i in scored],
                "incidents_skipped_as_duplicate": [
                    i.get("incident_id") for i in incidents if i.get("is_duplicate")
                ],
                "double_allocation_prevented": self._double_allocation_guard(allocations),
                "summary": summary["text"],
                "llm_used": summary["used_llm"],
                "disclaimer": (
                    "Recommendations only. No resource is dispatched by this system. "
                    "An authorised emergency commander must approve each allocation."
                ),
            },
            "_llm_used": summary["used_llm"],
        }

    # -- alternatives -------------------------------------------------------

    @staticmethod
    def _alternatives(
        shortages: list[dict[str, Any]],
        allocations: list[dict[str, Any]],
        resources: list[dict[str, Any]],
    ) -> list[str]:
        """Options that use only assets actually present in the registry."""
        options: list[str] = []

        for shortage in shortages:
            iid = shortage.get("incident_id") or "affected area"
            needed = shortage.get("required")
            have = shortage.get("available")
            deficit = shortage.get("deficit")
            options.append(
                f"ESCALATION REQUIRED for {iid}: {shortage.get('label')} deficit of "
                f"{deficit} {shortage.get('unit', 'units')} "
                f"(required {needed}, available {have}). "
                "Options limited to real-world actions: request mutual aid from a "
                "neighbouring district, escalate to district administration for "
                "surge assets, or request a higher response tier. "
                "The system will NOT assume additional units exist."
            )

            # Substituting with a longer-reach available unit is a legitimate
            # alternative only if that unit type is in the registry.
            if allocations:
                committed = {str(a.get("resource_id")) for a in allocations}
                spare = [
                    r
                    for r in resources
                    if enum_value(r.get("status")) == "available"
                    and str(r.get("resource_id")) not in committed
                    and not r.get("current_assignment")
                ]
                if spare:
                    names = ", ".join(
                        f"{r.get('resource_id')} ({enum_value(r.get('resource_type'))})"
                        for r in spare[:5]
                    )
                    options.append(
                        f"Re-task other available units for {iid}: {names}. Trade-off is "
                        "reduced reserve elsewhere and longer response times - "
                        "commander decision."
                    )
                else:
                    options.append(
                        f"No uncommitted unit remains in the registry, so {iid} cannot be "
                        "re-tasked. Mutual aid or a higher response tier is the only "
                        "remaining option - the system will not assume extra units exist."
                    )

        if not allocations and not shortages:
            options.append(
                "No allocation proposed: no incidents require planning at this time."
            )

        # Always offer the low-tech options.
        options.append(
            "Non-resource options the coordinator should consider: shelter-in-place "
            "advice, requesting transport authority clearance for a route priority "
            "corridor, and pre-emptive shelter occupancy increase to create buffer."
        )
        return options

    @staticmethod
    def _double_allocation_guard(allocations: list[dict[str, Any]]) -> dict[str, Any]:
        """Explicit self-check that no unit appears twice."""
        seen: dict[str, list[str]] = {}
        for alloc in allocations:
            rid = str(alloc.get("resource_id"))
            seen.setdefault(rid, []).append(str(alloc.get("incident_id")))
        duplicates = {k: v for k, v in seen.items() if len(v) > 1}
        return {
            "unique_resources": len(seen),
            "total_allocations": len(allocations),
            "duplicates_detected": duplicates,
            "passed": not duplicates,
        }

    # -- summary ------------------------------------------------------------

    async def _summarise(
        self,
        allocations: list[dict[str, Any]],
        shortages: list[dict[str, Any]],
        alternatives: list[str],
    ) -> dict[str, Any]:
        if allocations:
            deterministic = (
                f"{len(allocations)} allocation(s) proposed across "
                f"{len({a['incident_id'] for a in allocations})} incident(s). "
                + "; ".join(
                    f"{a['resource_id']} -> {a['incident_id']}" for a in allocations[:5]
                )
                + (". " if len(allocations) <= 5 else ". ")
                + (
                    f"{len(shortages)} shortage(s) detected requiring escalation. "
                    if shortages
                    else "No shortages detected. "
                )
                + "All proposals require emergency commander approval before dispatch."
            )
        else:
            deterministic = (
                f"No allocations proposed. {len(shortages)} shortage(s) detected. "
                "No suitable resource is available; escalation or mutual aid is required."
                if shortages
                else "No allocations required at this time."
            )

        llm = get_llm()
        result = await llm.complete_text(
            self.system_prompt,
            {
                "allocations": allocations,
                "shortages": shortages,
                "alternatives": alternatives,
                "deterministic_summary": deterministic,
            },
            fallback_text=deterministic,
        )
        return {"text": result.data.get("text") or deterministic, "used_llm": result.used_llm}