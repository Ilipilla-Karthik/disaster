"""Agent 2 - Situation Assessment & Severity Agent.

Consumes the incident records produced by intake plus the environmental and
accessibility context from the weather and geospatial agents, and produces a
transparent priority assessment.

The agent's job is interpretation and explanation. The *arithmetic* is done by
``app.services.priority_calculator`` - a documented deterministic model. This
agent never invents a number; it selects the model, feeds it verified inputs,
and narrates the result. When the LLM is available it adds plain-English
reasoning, but the score and severity it reports are still the deterministic
model's output.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.orchestration.state import WorkflowState
from app.services.priority_calculator import PriorityCalculator
from app.tools.llm_tool import get_llm

logger = logging.getLogger(__name__)

__all__ = ["SituationAssessmentAgent"]


class SituationAssessmentAgent(BaseAgent):
    name = "situation_assessment_severity"
    role = "Assess severity and produce transparent priority scoring"
    node_name = "assessment_output"
    uses_llm = True

    system_prompt = """
You explain an emergency priority assessment to a coordinator.

You will be given the deterministic factor breakdown of a priority score.
Your job is to explain it in plain English - what drove the score, what the
constraints are, and what a commander must decide.

Rules:
- Never state or imply a score different from the one supplied.
- Never invent resources, roads, capacities, casualty figures or locations.
- Always mention verification gaps explicitly.
- Keep the explanation under 120 words.
""".strip()

    def __init__(self) -> None:
        self.priority = PriorityCalculator()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        weather = (state.get("weather_output") or {}).get("per_incident") or {}
        access = (state.get("geospatial_output") or {}).get("access_by_incident") or {}
        assigned = state.get("assigned_counts") or {}
        plan_incidents = state.get("plan_incidents") or set()

        results: list[dict[str, Any]] = []
        llm_used = False

        for incident in incidents:
            iid = str(incident.get("incident_id"))
            result = self.priority.calculate(
                incident,
                weather=weather.get(iid),
                accessibility=access.get(iid),
                assigned_resource_count=int(assigned.get(iid, 0)),
                plan_exists=iid in plan_incidents,
            )
            payload = result.to_factors_payload()
            payload["incident_id"] = iid
            payload["weather"] = weather.get(iid) or {}
            payload["accessibility"] = access.get(iid) or {}

            narrative = await self._explain(payload)
            payload["narrative"] = narrative["text"]
            payload["llm_used"] = narrative["used_llm"]
            llm_used = llm_used or narrative["used_llm"]

            results.append(payload)

        escalated = [r for r in results if r.get("escalation_potential") not in {None, "none"}]
        needs_review = [
            r["incident_id"] for r in results if r.get("severity") == "verification_required"
        ]

        return {
            "assessments": results,
            "assessment_output": {
                "assessed": len(results),
                "escalation_watch": [r["incident_id"] for r in escalated],
                "verification_required": needs_review,
                "highest_priority": (
                    max(results, key=lambda r: r["score"])["incident_id"]
                    if results
                    else None
                ),
                "methodology": (
                    "Deterministic weighted factor model (8 factors, documented weights). "
                    "Severity bands: >=80 critical, >=60 high, >=40 moderate, "
                    ">=20 low, else verification_required. A band result of critical/high/"
                    "moderate is reported as verification_required until an authorised "
                    "source verifies the incident."
                ),
                "llm_used": llm_used,
            },
            "_llm_used": llm_used,
        }

    async def _explain(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deterministic summary first; LLM narrative is additive only."""
        top = [
            f
            for f in payload.get("factors", [])
            if isinstance(f, dict) and f.get("contribution", 0) > 0
        ]
        top_sorted = sorted(top, key=lambda f: f["contribution"], reverse=True)

        deterministic = (
            f"{payload['incident_id']} scores {payload['score']}/100 "
            f"(band '{payload.get('unverified_severity')}', reported as "
            f"'{payload['severity']}'). "
            + "; ".join(f"{f['label']} +{f['contribution']}" for f in top_sorted[:3])
            + ". "
            + (
                "Verification required for: "
                + ", ".join(payload["verification_required_fields"])
                + ". "
                if payload.get("verification_required_fields")
                else ""
            )
            + (
                f"Escalation potential: {payload['escalation_potential']}."
                if payload.get("escalation_potential") not in {None, "none"}
                else ""
            )
        )

        llm = get_llm()
        result = await llm.complete_text(
            self.system_prompt,
            {
                "incident_id": payload["incident_id"],
                "score": payload["score"],
                "severity": payload["severity"],
                "factors": top_sorted,
                "verification_required_fields": payload.get(
                    "verification_required_fields", []
                ),
                "escalation_potential": payload.get("escalation_potential"),
                "accessibility": (payload.get("accessibility") or {}).get("status_label"),
                "deterministic_summary": deterministic,
            },
            fallback_text=deterministic,
        )
        return {"text": result.data.get("text") or deterministic, "used_llm": result.used_llm}