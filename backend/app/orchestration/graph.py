"""LangGraph workflow for the multi-agent emergency response system.

Pipeline
--------
::

    intake -> weather -> geospatial -> assessment -> resource -> allocation
           -> coordination -> reviewer -> END

Why a linear graph: the dependency structure is genuinely linear (later agents
consume earlier agents' outputs), and a linear topology makes the audit trail
readable. The loops that matter are handled by *re-entering* the graph with a
``trigger`` rather than by adding cycles inside it - see ``replan_required``.

The reviewer is a hard gate: if it returns a blocking finding, the graph routes
to ``rejected`` instead of ``complete`` and the plan is never presented as
decision-ready.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.allocation_agent import ResourceAllocationAgent
from app.agents.assessment_agent import SituationAssessmentAgent
from app.agents.base import BaseAgent
from app.agents.coordination_agent import CoordinationAgent
from app.agents.geospatial_agent import GeospatialAgent
from app.agents.intake_agent import IncidentIntakeAgent
from app.agents.resource_agent import ResourceAgent
from app.agents.reviewer_agent import ReviewerAgent
from app.agents.weather_agent import WeatherAgent
from app.orchestration.state import WorkflowState, new_state

logger = logging.getLogger(__name__)

__all__ = [
    "AGENT_REGISTRY",
    "AGENT_NODES",
    "build_graph",
    "get_graph",
    "run_workflow",
    "run_agent_pipeline",
]

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "intake": IncidentIntakeAgent,
    "weather": WeatherAgent,
    "geospatial": GeospatialAgent,
    "assessment": SituationAssessmentAgent,
    "resource": ResourceAgent,
    "allocation": ResourceAllocationAgent,
    "coordination": CoordinationAgent,
    "reviewer": ReviewerAgent,
}

#: Graph node name -> agent class, in execution order.
AGENT_NODES: list[tuple[str, type[BaseAgent]]] = [
    ("intake", IncidentIntakeAgent),
    ("weather", WeatherAgent),
    ("geospatial", GeospatialAgent),
    ("assessment", SituationAssessmentAgent),
    ("resource", ResourceAgent),
    ("allocation", ResourceAllocationAgent),
    ("coordination", CoordinationAgent),
    ("reviewer", ReviewerAgent),
]


def _route_after_review(state: WorkflowState) -> str:
    review = state.get("reviewer_output") or {}
    if int(review.get("blocking_count") or 0) > 0:
        logger.warning(
            "Workflow %s blocked by reviewer agent (%d finding(s))",
            state.get("run_id"),
            review.get("blocking_count"),
        )
        return "changes_requested"
    return "complete"


def _finalize(state: WorkflowState) -> dict[str, Any]:
    review = state.get("reviewer_output") or {}
    blocked = int(review.get("blocking_count") or 0) > 0
    review_status = "changes_requested" if blocked else "approved"
    coordination = state.get("coordination_output") or {}
    if blocked:
        coordination = {
            **coordination,
            "status": "changes_requested",
            "disclaimer": (
                "The reviewer agent raised blocking findings. This plan was NOT "
                "presented as decision-ready and requires correction."
            ),
        }
    return {
        "coordination_output": coordination,
        "review_status": review_status,
        "finished": True,
        "trace": [
            {
                "agent": "workflow_finalizer",
                "role": "Gate the plan on reviewer verdict",
                "node": "finalize",
                "run_id": state.get("run_id"),
                "at": datetime.now(timezone.utc).isoformat(),
                "review_status": review_status,
            }
        ],
    }


def build_graph():
    """Compile the workflow graph."""
    graph = StateGraph(WorkflowState)

    for node_name, agent_cls in AGENT_NODES:
        agent = agent_cls()

        async def _run(state: WorkflowState, _agent: BaseAgent = agent) -> dict[str, Any]:
            return await _agent.run(state)

        graph.add_node(node_name, _run)

    graph.add_node("finalize", _finalize)

    chain = [node for node, _ in AGENT_NODES]
    for current, nxt in zip(chain, chain[1:]):
        graph.add_edge(current, nxt)
    graph.add_edge("reviewer", "finalize")
    graph.add_conditional_edges(
        "finalize", _route_after_review, {"complete": END, "changes_requested": END}
    )
    graph.set_entry_point("intake")

    return graph.compile()


_GRAPH = None


def get_graph():
    """Return the compiled graph, building it once on first use."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_workflow(
    *,
    incident_payloads: list[dict[str, Any]],
    roads: list[dict[str, Any]] | None = None,
    resources: list[dict[str, Any]] | None = None,
    shelters: list[dict[str, Any]] | None = None,
    assigned_counts: dict[str, int] | None = None,
    active_plan_ids: set[str] | None = None,
    plan_incidents: set[str] | None = None,
    trigger: str = "initial_assessment",
    scenario: str | None = None,
    run_id: str | None = None,
) -> WorkflowState:
    """Run the full multi-agent workflow and return the final state."""
    state = new_state(
        run_id=run_id or BaseAgent.new_run_id(),
        trigger=trigger,
        scenario=scenario,
        started_at=datetime.now(timezone.utc).isoformat(),
        incident_payloads=incident_payloads,
        roads=roads or [],
        resources=resources or [],
        shelters=shelters or [],
        assigned_counts=assigned_counts or {},
        active_plan_ids=active_plan_ids or set(),
        plan_incidents=plan_incidents or set(),
    )
    result = await get_graph().ainvoke(state)
    return result  # type: ignore[return-value]


async def run_agent_pipeline(state: WorkflowState) -> WorkflowState:
    """Run the pipeline against an already-populated state (used in tests)."""
    result = await get_graph().ainvoke(state)
    return result  # type: ignore[return-value]