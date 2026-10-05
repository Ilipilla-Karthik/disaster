"""Operator assistant (Requirement 13).

Design constraints, in priority order:

1. **Read-only.** This service never writes. It cannot create incidents, approve
   plans, or dispatch resources - those are REST endpoints guarded by
   ``require_commander`` with an audit trail.
2. **Answer from data, not from memory.** Every answer is built from the live
   database first. Where a number comes from a query, the answer says so.
3. **Say "verification required".** If the underlying record is unverified, the
   answer says that instead of smoothing it over.
4. **LLM is optional narration.** ``get_llm`` may rephrase the deterministic
   answer. If it fails or is not configured, the deterministic answer is served
   and the response reports ``llm_used=false``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AlertSeverity,
    IncidentType,
    PlanStatus,
    ResourceStatus,
    RouteStatus,
    ShelterStatus,
    VerificationStatus,
)
from app.models.models import Alert, Incident, Resource, ResponsePlan, Road, Shelter
from app.tools.llm_tool import get_llm

logger = logging.getLogger(__name__)

__all__ = ["ChatService"]


class ChatService:
    """Question answering over the operational picture. Read-only by construction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.llm = get_llm()

    # -- public API ---------------------------------------------------------

    async def answer(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        actor_role: str = "viewer",
    ) -> dict[str, Any]:
        question = message.strip()
        intent = _classify(question)

        try:
            deterministic, citations = await self._dispatch(intent, question)
        except Exception as exc:  # a query failure must not become an invented answer
            logger.exception("Chat intent %s failed", intent)
            return {
                "answer": (
                    "I could not read the operational picture for that question "
                    f"({type(exc).__name__}). No data is being guessed - please retry "
                    "or consult the dashboard."
                ),
                "citations": [],
                "intent": intent,
                "llm_used": False,
                "confidence": "low",
                "conversation_id": conversation_id,
                "limitations": ["Query failed; no figures reported."],
            }

        narration = await self._narrate(question, deterministic, intent)
        confidence = "high" if citations else "low"
        return {
            "answer": narration,
            "data_answer": deterministic,
            "citations": citations,
            "intent": intent,
            "llm_used": narration != deterministic,
            "confidence": confidence,
            "conversation_id": conversation_id,
            "actor_role": actor_role,
            "limitations": _LIMITATIONS.get(intent, []),
            "note": (
                "This assistant is read-only. Plan approval, dispatch, and incident "
                "creation are human-authorised operations on the REST API."
            ),
        }

    # -- deterministic answers ---------------------------------------------

    async def _dispatch(
        self, intent: str, question: str
    ) -> tuple[str, list[dict[str, str]]]:
        refusal = self._refusal_if_out_of_scope(question)
        if refusal is not None:
            return refusal
        handler = {
            "unverified_incidents": self._answer_unverified,
            "shelter_capacity": self._answer_shelters,
            "available_resources": self._answer_resources,
            "plan_status": self._answer_plans,
            "road_conditions": self._answer_roads,
            "alerts": self._answer_alerts,
            "capacity_summary": self._answer_capacity_summary,
        }.get(intent)
        if handler is not None:
            return await handler()
        if intent == "plan_status":
            return await self._answer_plans()
        return await self._answer_overview(question)

    async def _answer_unverified(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(
            await self.session.scalars(
                select(Incident).where(
                    Incident.verification_status != VerificationStatus.VERIFIED
                )
            )
        )
        if not rows:
            return (
                "Every incident on file is verified. No unverified reports are "
                "outstanding.",
                [],
            )
        lines = [
            f"{len(rows)} incident(s) are not yet verified:",
        ]
        citations: list[dict[str, str]] = []
        for inc in rows[:12]:
            missing = ", ".join(inc.missing_fields or []) or "no fields flagged"
            lines.append(
                f"- {inc.incident_id} ({inc.incident_type.value}) at {inc.location}: "
                f"{inc.verification_status.value}. Outstanding: {missing}."
            )
            citations.append(
                {
                    "source": "incidents table",
                    "reference": inc.incident_id,
                    "detail": f"verification_status={inc.verification_status.value}",
                }
            )
        lines.append(
            "These fields are being treated as unknown until a human verifies them; "
            "they are not being assumed either way."
        )
        return "\n".join(lines), citations

    async def _answer_shelters(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(await self.session.scalars(select(Shelter).order_by(Shelter.shelter_id)))
        if not rows:
            return "No shelters are on file.", []
        citations: list[dict[str, str]] = []
        lines = ["Shelter capacity:"]
        for s in rows:
            pct = (s.current_occupancy / s.capacity * 100) if s.capacity else 0.0
            flag = " - NEAR CAPACITY" if pct >= 85 else ""
            lines.append(
                f"- {s.shelter_id} {s.name}: {s.current_occupancy}/{s.capacity} "
                f"occupied ({pct:.0f}%), status {s.operational_status.value}"
                f"{flag}."
            )
            citations.append(
                {
                    "source": "shelters table",
                    "reference": s.shelter_id,
                    "detail": f"current_occupancy={s.current_occupancy}",
                }
            )
        return "\n".join(lines), citations

    async def _answer_resources(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(await self.session.scalars(select(Resource).order_by(Resource.resource_id)))
        by_status: dict[str, list[Resource]] = {}
        for r in rows:
            by_status.setdefault(r.status.value, []).append(r)
        if not rows:
            return "No resources are on file.", []
        citations: list[dict[str, str]] = []
        lines = [f"{len(rows)} resource(s) on file, by state:"]
        for status_value in sorted(by_status):
            group = by_status[status_value]
            total_capacity = sum(r.capacity for r in group)
            lines.append(
                f"- {status_value}: {len(group)} unit(s), "
                f"{total_capacity} total capacity"
            )
            if status_value == ResourceStatus.AVAILABLE.value:
                for r in group:
                    lines.append(
                        f"    {r.resource_id} ({r.resource_type.value}) at "
                        f"{r.location}, capacity {r.capacity}"
                    )
                    citations.append(
                        {
                            "source": "resources table",
                            "reference": r.resource_id,
                            "detail": f"status={r.status.value}",
                        }
                    )
        lines.append(
            "Available units are candidates only. Nothing is dispatched until a plan "
            "is approved and an action is started by an authorised officer."
        )
        return "\n".join(lines), citations

    async def _answer_plans(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(
            await self.session.scalars(
                select(ResponsePlan).order_by(ResponsePlan.created_at.desc()).limit(10)
            )
        )
        if not rows:
            return "No response plans have been generated yet.", []
        citations: list[dict[str, str]] = []
        lines = ["Most recent plans:"]
        for p in rows:
            lines.append(
                f"- {p.plan_id} '{p.title}': status {p.status.value}, "
                f"{len(p.incident_ids or [])} incident(s), "
                f"{len(p.allocations or [])} recommendation(s)"
                + (f", approved by {p.approved_by}" if p.approved_by else ", awaiting approval")
                + (f" (superseded by {p.superseded_by})" if p.superseded_by else "")
                + "."
            )
            citations.append(
                {"source": "response_plans table", "reference": p.plan_id,
                 "detail": f"status={p.status.value}"}
            )
        return "\n".join(lines), citations

    async def _answer_roads(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(await self.session.scalars(select(Road).order_by(Road.road_id)))
        unknown = [r for r in rows if r.status == RouteStatus.UNKNOWN.value]
        blocked = [r for r in rows if r.status in {RouteStatus.BLOCKED.value, RouteStatus.CLOSED.value}]
        citations: list[dict[str, str]] = []
        lines = []
        if blocked:
            lines.append("Roads reported closed or blocked by an authority:")
            for r in blocked:
                lines.append(
                    f"- {r.road_id} {r.name} ({r.location}): {r.status.value}"
                    + (f" - {r.blocked_reason}" if r.blocked_reason else "")
                    + f", reported by {r.reported_by or 'unknown source'}."
                )
                citations.append(
                    {"source": "roads table", "reference": r.road_id,
                     "detail": f"fact_status={r.fact_status.value}"}
                )
        if unknown:
            lines.append(
                f"{len(unknown)} road(s) have no authoritative condition report. "
                "Unknown is not being treated as open."
            )
            for r in unknown:
                citations.append(
                    {"source": "roads table", "reference": r.road_id,
                     "detail": "status=unknown"}
                )
        if not lines:
            lines.append(
                "No road is recorded as closed, and every road on file has an "
                "authoritative condition report."
            )
        return "\n".join(lines), citations

    async def _answer_alerts(self) -> tuple[str, list[dict[str, str]]]:
        rows = list(
            await self.session.scalars(
                select(Alert).where(Alert.is_active.is_(True)).order_by(
                    Alert.created_at.desc()
                )
            )
        )
        if not rows:
            return "No active alerts.", []
        lines = [f"{len(rows)} active alert(s):"]
        citations: list[dict[str, str]] = []
        for a in rows[:15]:
            lines.append(
                f"- [{a.severity.value}] {a.alert_type.value}: {a.message}"
                + ("" if not a.acknowledged else " (acknowledged)")
            )
            citations.append(
                {"source": "alerts table", "reference": a.alert_id,
                 "detail": f"severity={a.severity.value}"}
            )
        return "\n".join(lines), citations

    async def _answer_capacity_summary(self) -> tuple[str, list[dict[str, str]]]:
        resources = list(await self.session.scalars(select(Resource)))
        shelters = list(await self.session.scalars(select(Shelter)))
        incidents = list(await self.session.scalars(select(Incident)))
        available = [r for r in resources if r.status == ResourceStatus.AVAILABLE]
        return (
            f"Current picture: {len(incidents)} incident(s), "
            f"{len(resources)} resource(s) ({len(available)} available), "
            f"{len(shelters)} shelter(s) with "
            f"{sum(s.current_occupancy for s in shelters)} people "
            f"across {sum(s.capacity for s in shelters)} places.",
            [
                {"source": "incidents/resources/shelters",
                 "detail": "counts computed at query time"}
            ],
        )

    async def _answer_overview(self, question: str) -> tuple[str, list[dict[str, str]]]:
        """Default: a grounded summary, plus a pointer to what I cannot see."""
        answer, citations = await self._answer_capacity_summary()
        return (
            f"{answer}\n\nI can answer questions about incidents and verification, "
            "shelter occupancy, resource availability, plan and approval status, "
            "road conditions, and active alerts. I cannot invent information: if it "
            "is not in the operational picture, I will say verification is required.",
            citations,
        )

    # -- refusals -----------------------------------------------------------

    def _refusal_if_out_of_scope(
        self, question: str
    ) -> tuple[str, list[dict[str, str]]] | None:
        """Decline requests for actions this system must not take.

        The operator console should never be able to talk the assistant into
        dispatching a unit, approving a plan or ordering an evacuation. The
        refusal explains where the real control lives, rather than just saying no.
        """
        text = question.lower()
        for pattern, reason in _OUT_OF_SCOPE:
            if pattern.search(text):
                return (
                    f"I cannot {reason}. That is a decision for an authorised "
                    "emergency commander, recorded against their name in the "
                    "audit trail - not something an assistant can do on request.\n\n"
                    "What I can do: explain the current situation, show what is "
                    "unverified, list available resources and shelter capacity, "
                    "and summarise plans and their approval state.",
                    [],
                )
        return None

    # -- optional narration -------------------------------------------------

    async def _narrate(self, question: str, deterministic: str, intent: str) -> str:
        """Optionally rephrase the deterministic answer. Facts are never the model's."""
        result = await self.llm.complete_text(
            system_prompt=(
                "You are an assistant embedded in an emergency decision-support "
                "system. Explain the supplied operational answer to the operator in "
                "plain prose. Do not add numbers, resources, roads, or incidents "
                "that are absent from the answer, and do not recommend dispatching "
                "anything."
            ),
            user_payload={"question": question, "verified_answer": deterministic},
            fallback_text=deterministic,
        )
        if not result.used_llm or not result.data.get("text"):
            return deterministic
        text = str(result.data["text"]).strip()
        # Guard against a model that fabricates identifiers.
        identifiers = set(re.findall(r"\b(?:INC|RES|SH|RD|PLN|ALT)-\w+\b", deterministic))
        invented = set(re.findall(r"\b(?:INC|RES|SH|RD|PLN|ALT)-\w+\b", text)) - identifiers
        if invented:
            logger.warning("Discarding LLM narrative: introduced unknown ids %s", invented)
            return deterministic
        return text


# -- intent classification ----------------------------------------------

_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "unverified_incidents",
        re.compile(r"\b(unverified|verify|verification|pending|incomplete|outstanding)\b", re.I),
    ),
    ("shelter_capacity", re.compile(r"\b(shelter|capacity|occupanc|refugee|displaced)\w*\b", re.I)),
    (
        "available_resources",
        re.compile(r"\b(resource|unit|vehicle|crew|team|boat|truck|available|deployable)\w*\b", re.I),
    ),
    ("plan_status", re.compile(r"\b(plan|approval|approve|pending plan|status of)\b", re.I)),
    ("road_conditions", re.compile(r"\b(road|route|closed|blocked|access|passable)\w*\b", re.I)),
    ("alerts", re.compile(r"\b(alert|warning|critical|escalat)\w*\b", re.I)),
    (
        "capacity_summary",
        re.compile(r"\b(summary|overview|current situation|status|sitrep|situation report)\b", re.I),
    ),
]

# Requests that ask the assistant to take an operational action. Matching any
# of these produces a refusal regardless of how polite or urgent the phrasing.
_OUT_OF_SCOPE: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(dispatch|deploy|send|mobilis|mobiliz|launch|position)\b"
            r"[^\n]{0,30}\b(unit|units|resource|resources|vehicle|vehicles|"
            r"team|teams|crew|boat|truck|helicopter|boat)?",
            re.I,
        ),
        "dispatch resources",
    ),
    (
        re.compile(r"\b(approve|authoris|authoriz|sign off|greenlight)\b[^\n]{0,20}\bplan", re.I),
        "approve a plan",
    ),
    (re.compile(r"\b(issue|order|start)\b[^\n]{0,20}\bevacuation", re.I), "order an evacuation"),
    (
        re.compile(r"\b(triage|diagnos|prescrib|treat)\b", re.I),
        "perform medical triage",
    ),
    (
        re.compile(r"\b(overrid|bypass|ignore)\b[^\n]{0,30}\b(commander|safety|rule|policy|check)", re.I),
        "override a safety check or an officer",
    ),
]


_LIMITATIONS: dict[str, list[str]] = {
    "unverified_incidents": [
        "Unverified fields are reported as unknown, not estimated."
    ],
    "shelter_capacity": [
        "Occupancy reflects recorded changes only; the shelter log is the source of truth."
    ],
    "available_resources": [
        "Availability does not imply suitability. Capability and access matching is done "
        "by the allocation engine.",
        "No resource is dispatched by asking a question."
    ],
    "plan_status": [
        "Only an authorised commander can approve a plan; this answer cannot change one."
    ],
    "road_conditions": [
        "Road status reflects authority reports only. Agent inferences are never presented "
        "as road facts.",
    ],
    "alerts": ["Alerts are operational signals, not confirmation of ground truth."],
    "capacity_summary": ["Figures are computed at query time from the live tables."],
}


def _classify(message: str) -> str:
    for intent, pattern in _INTENTS:
        if pattern.search(message):
            return intent
    return "overview"