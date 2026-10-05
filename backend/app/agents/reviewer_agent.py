"""Agent 8 - Reviewer / Critic Agent (quality gate).

Adversarially reviews the assembled plan *before* a human ever sees it, and can
reject it. It exists because the failure modes of a multi-agent system are
specific and checkable:

* an agent invented a resource, a route, or a capacity figure,
* the same unit was allocated twice,
* unverified data was presented as fact,
* a simulated weather figure was treated as an observation,
* the plan asserts an action as already executed,
* a duplicate report was double-counted.

Checks that can be evaluated mechanically are evaluated mechanically. The LLM
critique is additive and can never mark a check as passed.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.orchestration.state import WorkflowState
from app.tools.llm_tool import get_llm

logger = logging.getLogger(__name__)

__all__ = ["ReviewerAgent"]

SEVERITY_ORDER = {"critical": 4, "high": 3, "moderate": 2, "low": 1}


class ReviewerAgent(BaseAgent):
    name = "reviewer_critic"
    role = "Adversarially review the plan and enforce safety invariants"
    node_name = "review_output"
    uses_llm = True

    system_prompt = """
You are a safety reviewer for an emergency decision-support system. You are
looking for evidence of overreach: invented facts, hidden uncertainty, resource
double-allocation, or language implying the system took autonomous action.

Rules:
- Only raise a concern supported by the supplied data.
- Never invent facts of your own.
- Do not rewrite the plan; report what is wrong and why.
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resources = state.get("resources") or []
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        assessments = state.get("assessments") or []
        allocation = state.get("allocation_output") or {}
        weather = state.get("weather_output") or {}
        geo = state.get("geospatial_output") or {}
        coordination = state.get("coordination_output") or {}
        actions = state.get("actions") or []

        findings: list[dict[str, Any]] = []

        # -- CHK-01: every allocated unit exists in the registry ----------
        known_ids = {str(r.get("resource_id")) for r in resources}
        allocations = allocation.get("allocations") or []
        invented = [
            a.get("resource_id")
            for a in allocations
            if str(a.get("resource_id")) not in known_ids
        ]
        if invented:
            findings.append(
                {
                    "check_id": "CHK-01",
                    "name": "no_invented_resources",
                    "severity": "blocking",
                    "finding": f"Allocations reference resources absent from the registry: {invented}.",
                }
            )

        # -- CHK-02: no double allocation ---------------------------------
        usage: dict[str, list[str]] = {}
        for alloc in allocations:
            usage.setdefault(str(alloc.get("resource_id")), []).append(
                str(alloc.get("incident_id"))
            )
        doubled = {k: v for k, v in usage.items() if len(v) > 1}
        if doubled:
            findings.append(
                {
                    "check_id": "CHK-02",
                    "name": "no_double_allocation",
                    "severity": "blocking",
                    "finding": f"Resources allocated to multiple incidents simultaneously: {doubled}.",
                }
            )

        # -- CHK-03: unverified input is not presented as fact ------------
        # The documented gate: an unverified incident whose band reaches
        # moderate/high/critical must be reported as verification_required. Low
        # bands carry no escalation claim, so they are not violations.
        escalation_bands = {"critical", "high", "moderate"}
        misreported = [
            a.get("incident_id")
            for a in assessments
            if a.get("unverified_severity") in escalation_bands
            and a.get("severity") != "verification_required"
        ]
        if misreported:
            findings.append(
                {
                    "check_id": "CHK-03",
                    "name": "unverified_not_presented_as_fact",
                    "severity": "blocking",
                    "finding": (
                        "Unverified incidents reported as confirmed severity: "
                        f"{misreported}."
                    ),
                }
            )

        # -- CHK-04: simulated weather is labelled -------------------------
        if weather.get("simulation_mode"):
            snapshot = weather.get("snapshot") or {}
            if not snapshot.get("simulation_mode"):
                findings.append(
                    {
                        "check_id": "CHK-04",
                        "name": "simulated_weather_labelled",
                        "severity": "blocking",
                        "finding": "Simulated weather reached the plan snapshot without its simulation label.",
                    }
                )

        # -- CHK-05: inferred accessibility is not stated as fact ---------
        for iid, access in (geo.get("access_by_incident") or {}).items():
            observations = access.get("inferred_observations") or []
            for obs in observations:
                if obs.get("is_fact"):
                    findings.append(
                        {
                            "check_id": "CHK-05",
                            "name": "inference_not_marked_as_fact",
                            "severity": "blocking",
                            "finding": (
                                f"{iid}: inferred observation recorded as fact: {obs!r}."
                            ),
                        }
                    )

        # -- CHK-06: no autonomous dispatch --------------------------------
        dispatched = [
            a for a in actions if str(a.get("status")) in {"dispatched", "in_progress", "completed"}
        ]
        if dispatched:
            findings.append(
                {
                    "check_id": "CHK-06",
                    "name": "no_autonomous_dispatch",
                    "severity": "blocking",
                    "finding": (
                        f"{len(dispatched)} action(s) are marked as executed. This system "
                        "produces recommendations only and cannot dispatch resources."
                    ),
                }
            )

        # -- CHK-07: plan awaits approval ----------------------------------
        plan_status = str(coordination.get("status") or "")
        if plan_status not in {"awaiting_approval", "draft", ""}:
            findings.append(
                {
                    "check_id": "CHK-07",
                    "name": "awaiting_human_approval",
                    "severity": "blocking",
                    "finding": f"Plan status is '{plan_status}' rather than 'awaiting_approval'.",
                }
            )

        # -- CHK-08: duplicates not double-counted ------------------------
        double_counted = [
            str(incident.get("incident_id"))
            for incident in incidents
            if incident.get("is_duplicate")
            and str(incident.get("incident_id")) in (coordination.get("canonical_incidents") or [])
        ]
        if double_counted:
            findings.append(
                {
                    "check_id": "CHK-08",
                    "name": "duplicates_not_double_counted",
                    "severity": "blocking",
                    "finding": f"Duplicate reports were counted as canonical incidents: {double_counted}.",
                }
            )

        # -- CHK-09: verification gaps surfaced ---------------------------
        if assessments and not any(
            a.get("verification_required_fields") for a in assessments
        ):
            if any(a.get("severity") == "verification_required" for a in assessments):
                findings.append(
                    {
                        "check_id": "CHK-09",
                        "name": "verification_gaps_visible",
                        "severity": "warning",
                        "finding": "Incidents require verification but no specific fields were identified.",
                    }
                )

        critique = await self._llm_critique(
            assessments, allocation, coordination, findings
        )

        blocking = [f for f in findings if f["severity"] == "blocking"]
        status = "changes_requested" if blocking else "approved"

        return {
            "review_status": status,
            "review_findings": findings,
            "reviewer_output": {
                "status": status,
                "findings": findings,
                "blocking_count": len(blocking),
                "warning_count": len(findings) - len(blocking),
                "critique": critique["text"],
                "llm_used": critique["used_llm"],
                "checks_performed": [
                    "CHK-01 no_invented_resources",
                    "CHK-02 no_double_allocation",
                    "CHK-03 unverified_not_presented_as_fact",
                    "CHK-04 simulated_weather_labelled",
                    "CHK-05 inference_not_marked_as_fact",
                    "CHK-06 no_autonomous_dispatch",
                    "CHK-07 awaiting_human_approval",
                    "CHK-08 duplicates_not_double_counted",
                    "CHK-09 verification_gaps_visible",
                ],
                "safety_prohibitions_enforced": list(BaseAgent.PROHIBITIONS),
                "verdict": (
                    "REJECTED - the plan must not be shown as decision-ready until the "
                    "blocking findings are resolved."
                    if blocking
                    else "APPROVED FOR HUMAN REVIEW - this is not authorisation to act."
                ),
            },
            "_llm_used": critique["used_llm"],
        }

    async def _llm_critique(
        self,
        assessments: list[dict[str, Any]],
        allocation: dict[str, Any],
        coordination: dict[str, Any],
        findings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        fallback = (
            "All mechanical safety checks passed. The plan remains a recommendation "
            "requiring authorised human approval."
            if not findings
            else f"{len(findings)} finding(s) raised; see checks for detail."
        )
        llm = get_llm()
        result = await llm.complete_text(
            self.system_prompt,
            {
                "assessments": assessments,
                "allocations": (allocation.get("allocations") or [])[:20],
                "shortages": allocation.get("shortages") or [],
                "plan_summary": coordination.get("summary"),
                "assumptions": coordination.get("assumptions"),
                "mechanical_findings": findings,
            },
            fallback_text=fallback,
        )
        return {"text": result.data.get("text") or fallback, "used_llm": result.used_llm}