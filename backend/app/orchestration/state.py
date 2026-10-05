"""Shared workflow state for the multi-agent graph.

This ``WorkflowState`` is the single, explicit communication channel between
agents. Agents never call each other's Python methods directly - they read what
they need from the state and write their contribution back, which is what makes
the pipeline auditable and replayable.

Keys are grouped as:

``operational_picture``  read-only snapshot of the world at run start
``<agent>_output``       one slot per agent - the agent-to-agent contract
``derived``              accumulated results (assessments, allocations, ...)
``trace``                per-node audit log: who ran, in what order, with what
``control``              run metadata, human-gate flags, iteration counters
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

__all__ = ["WorkflowState", "new_state", "merge_dict"]


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """Reducer for dict keys: later writes merge into earlier ones."""
    merged: dict[str, Any] = dict(left or {})
    merged.update(right or {})
    return merged


def extend_list(
    left: list | None, right: list | None
) -> list:
    """Reducer for append-only list keys (assessments, trace, messages)."""
    return [*(left or []), *(right or [])]


class WorkflowState(TypedDict, total=False):
    # ---- run metadata -----------------------------------------------------
    run_id: str
    trigger: str
    scenario: str | None
    started_at: str

    # ---- operational picture (input) -------------------------------------
    incident_payloads: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    roads: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    shelters: list[dict[str, Any]]
    active_plan_ids: set[str]
    assigned_counts: dict[str, int]
    plan_incidents: set[str]

    # ---- agent outputs (the agent-to-agent contract) ----------------------
    intake_output: dict[str, Any]
    weather_output: dict[str, Any]
    geospatial_output: dict[str, Any]
    assessment_output: dict[str, Any]
    resource_output: dict[str, Any]
    allocation_output: dict[str, Any]
    coordination_output: dict[str, Any]
    reviewer_output: dict[str, Any]

    # ---- derived / accumulated -------------------------------------------
    normalized_incidents: list[dict[str, Any]]
    duplicate_links: list[dict[str, Any]]
    assessments: Annotated[list[dict[str, Any]], extend_list]
    allocations: list[dict[str, Any]]
    shortages: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    assumptions: list[str]
    alternatives: list[str]
    unresolved_issues: list[str]
    review_status: str
    review_findings: list[dict[str, Any]]
    agent_errors: Annotated[dict[str, str], merge_dict]

    # ---- control ---------------------------------------------------------
    requires_approval: bool
    replan_required: bool
    trigger_road_change: dict[str, Any] | None
    iteration: int
    finished: bool

    # ---- observability ---------------------------------------------------
    trace: Annotated[list[dict[str, Any]], extend_list]
    messages: Annotated[list[Any], add_messages]


def new_state(**kwargs: Any) -> WorkflowState:
    """Build a fully-populated state with sane defaults."""
    state: dict[str, Any] = {
        "run_id": "",
        "trigger": "initial_assessment",
        "scenario": None,
        "started_at": "",
        "incident_payloads": [],
        "incidents": [],
        "roads": [],
        "resources": [],
        "shelters": [],
        "active_plan_ids": set(),
        "assigned_counts": {},
        "plan_incidents": set(),
        "intake_output": {},
        "weather_output": {},
        "geospatial_output": {},
        "assessment_output": {},
        "resource_output": {},
        "allocation_output": {},
        "coordination_output": {},
        "reviewer_output": {},
        "normalized_incidents": [],
        "duplicate_links": [],
        "assessments": [],
        "allocations": [],
        "shortages": [],
        "alerts": [],
        "actions": [],
        "assumptions": [],
        "alternatives": [],
        "unresolved_issues": [],
        "review_status": "",
        "review_findings": [],
        "agent_errors": {},
        "requires_approval": True,
        "replan_required": False,
        "trigger_road_change": None,
        "iteration": 0,
        "finished": False,
        "trace": [],
        "messages": [],
    }
    state.update(kwargs)
    return state  # type: ignore[return-value]