"""Plans, approvals, actions and replanning (Requirements 7, 8, 11).

``POST /plans/generate`` runs the eight-agent workflow over the current
operational picture. Everything it returns is a recommendation; a plan only
becomes active through an explicit approval decision by a named officer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, hub, require_commander
from app.models.enums import (
    AlertSeverity,
    AlertType,
    PlanStatus,
    ResourceStatus,
)
from app.models.models import (
    Action,
    Incident,
    Resource,
    Road,
    Shelter,
)
from app.orchestration.graph import run_workflow
from app.services.alert_service import AlertService
from app.services.plan_service import PlanRejected, PlanService
from app.services.reporting_service import DashboardService, ReportingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])


async def _snapshot(session: AsyncSession) -> dict[str, Any]:
    """Load the operational picture the agents will reason about."""
    incidents = list(await session.scalars(select(Incident)))
    resources = list(await session.scalars(select(Resource)))
    shelters = list(await session.scalars(select(Shelter)))
    roads = list(await session.scalars(select(Road)))
    services = PlanService(session)

    return {
        "incident_payloads": [
            {
                "incident_id": i.incident_id,
                "incident_type": i.incident_type.value,
                "location": i.location,
                "latitude": i.latitude,
                "longitude": i.longitude,
                "description": i.description,
                "raw_text": i.raw_report,
                "people_reported_affected": i.people_reported_affected,
                "infrastructure_issue": i.infrastructure_issue,
                "assistance_requested": i.assistance_requested,
                "source": i.source,
                "source_type": i.source_type.value if i.source_type else None,
                "confidence": i.confidence.value,
                "verification_status": i.verification_status.value,
                "reported_at": i.reported_at.isoformat() if i.reported_at else None,
                "is_duplicate": i.is_duplicate,
                "duplicate_of": i.duplicate_of,
            }
            for i in incidents
        ],
        "roads": [
            {
                "road_id": r.road_id,
                "name": r.name,
                "origin_zone": r.origin_zone,
                "destination_zone": r.destination_zone,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "status": r.status.value,
                "fact_status": r.fact_status.value,
                "blocked_reason": r.blocked_reason,
            }
            for r in roads
        ],
        "resources": [
            {
                "resource_id": r.resource_id,
                "resource_type": r.resource_type.value,
                "location": r.location,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "capacity": r.capacity,
                "status": r.status.value,
                "capability_tags": r.capability_tags or [],
                "access_modes": r.access_modes or [],
                "current_assignment": r.current_assignment,
            }
            for r in resources
        ],
        "shelters": [
            {
                "shelter_id": s.shelter_id,
                "name": s.name,
                "capacity": s.capacity,
                "current_occupancy": s.current_occupancy,
                "operational_status": s.operational_status.value,
                "latitude": s.latitude,
                "longitude": s.longitude,
            }
            for s in shelters
        ],
        "assigned_counts": await services.assigned_counts(),
        "plan_incidents": set(await services.incident_plan_status([i.incident_id for i in incidents])),
    }


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_plan(
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
    scenario: str | None = Query(default=None),
    trigger: str = Query(default="initial_assessment"),
    persist: bool = Query(default=True),
) -> dict[str, Any]:
    """Run the multi-agent workflow and persist the resulting plan as a draft."""
    snapshot = await _snapshot(session)
    state = await run_workflow(
        incident_payloads=snapshot["incident_payloads"],
        roads=snapshot["roads"],
        resources=snapshot["resources"],
        shelters=snapshot["shelters"],
        assigned_counts=snapshot["assigned_counts"],
        plan_incidents=snapshot["plan_incidents"],
        trigger=trigger,
        scenario=scenario,
    )

    await _persist_assessments(session, state)
    alerts = AlertService(session)
    await alerts.evaluate_shortages(state.get("shortages") or [])
    await alerts.evaluate_roads(snapshot["roads"])

    plan_payload = None
    if persist:
        plan = await PlanService(session).persist_plan(state, requested_by=actor["user"])
        plan_payload = (await PlanService(session).get_plan_detail(plan.plan_id))["plan"]
        await alerts.raise_alert(
            AlertType.APPROVAL_REQUIRED,
            f"Plan {plan.plan_id} awaits human approval "
            f"({len(plan.allocations)} recommendation(s)).",
            severity=AlertSeverity.WARNING,
            context={"plan_id": plan.plan_id},
        )
        await alerts.raise_alert(
            AlertType.REPLAN_TRIGGERED if trigger != "initial_assessment"
            else AlertType.SEVERITY_ESCALATED,
            f"Plan {plan.plan_id} generated by trigger '{trigger}'.",
            severity=AlertSeverity.INFO,
            context={"plan_id": plan.plan_id, "trigger": trigger},
        )
    await session.commit()

    await hub.broadcast(
        "alerts",
        {
            "type": "plan_generated",
            "plan_id": plan_payload["plan_id"] if plan_payload else None,
            "status": (plan_payload or {}).get("status"),
            "review_status": state.get("review_status"),
            "run_id": state.get("run_id"),
            "allocations": len(state.get("allocations") or []),
            "shortages": len(state.get("shortages") or []),
            "requires_approval": True,
        },
    )

    return {
        "run_id": state.get("run_id"),
        "review_status": state.get("review_status"),
        "review_output": state.get("reviewer_output"),
        "coordination_output": state.get("coordination_output"),
        "assessments": state.get("assessments"),
        "allocations": state.get("allocations"),
        "shortages": state.get("shortages"),
        "alternatives": state.get("alternatives"),
        "unresolved_issues": state.get("unresolved_issues"),
        "assumptions": state.get("assumptions"),
        "trace": state.get("trace"),
        "agent_errors": state.get("agent_errors"),
        "plan": plan_payload,
        "disclaimer": (
            "Recommendations only. No resource has been dispatched. An authorised "
            "emergency commander must approve the plan."
        ),
    }


async def _persist_assessments(session: AsyncSession, state: dict[str, Any]) -> None:
    """Write scores and verification-gated severities back onto incidents."""
    from datetime import datetime, timezone

    for assessment in state.get("assessments") or []:
        incident = await session.scalar(
            select(Incident).where(Incident.incident_id == assessment.get("incident_id"))
        )
        if incident is None:
            continue
        incident.priority_score = float(assessment.get("score") or 0.0)
        incident.severity_level = assessment.get("severity")
        incident.priority_factors = {
            "factors": assessment.get("factors"),
            "severity": assessment.get("severity"),
            "unverified_severity": assessment.get("unverified_severity"),
            "narrative": assessment.get("narrative"),
            "methodology": assessment.get("methodology"),
            "escalation_potential": assessment.get("escalation_potential"),
            "verification_required_fields": assessment.get("verification_required_fields"),
        }
        incident.last_reassessed_at = datetime.now(timezone.utc)
    await session.flush()


@router.get("")
async def list_plans(
    session: AsyncSession = Depends(get_session),
    status_filter: PlanStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    plans = await PlanService(session).list_plans(limit=limit)
    if status_filter:
        plans = [p for p in plans if p.status == status_filter]
    return {
        "total": len(plans),
        "items": [
            {
                "plan_id": p.plan_id,
                "title": p.title,
                "status": p.status.value,
                "trigger": p.trigger,
                "summary": p.summary,
                "incident_ids": p.incident_ids,
                "allocation_count": len(p.allocations or []),
                "shortage_count": len(p.shortages or []),
                "approved_by": p.approved_by,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "superseded_by": p.superseded_by,
                "reviewer_status": (p.reviewer_findings or {}).get("status"),
            }
            for p in plans
        ],
    }


@router.get("/approvals")
async def list_approvals(
    session: AsyncSession = Depends(get_session),
    plan_id: str | None = Query(default=None),
) -> dict[str, Any]:
    requests = await PlanService(session).list_approvals(plan_id)
    return {
        "total": len(requests),
        "items": [
            {
                "request_id": r.request_id,
                "plan_id": r.plan_id,
                "status": r.status.value,
                "requested_from": r.requested_from,
                "decided_by": r.decided_by,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                "decision": r.decision,
            }
            for r in requests
        ],
    }


@router.get("/{plan_id}")
async def get_plan(plan_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    detail = await PlanService(session).get_plan_detail(plan_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return detail


@router.post("/{plan_id}/approve-request")
async def request_approval(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    service = PlanService(session)
    try:
        request = await service.create_approval_request(
            plan_id, requested_from="Emergency Commander"
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await AlertService(session).raise_alert(
        AlertType.APPROVAL_REQUIRED,
        f"Plan {plan_id} awaits approval.",
        severity=AlertSeverity.WARNING,
        context={"plan_id": plan_id},
    )
    await session.commit()
    return {
        "request_id": request.request_id,
        "plan_id": plan_id,
        "status": request.status.value,
        "requested_from": request.requested_from,
        "original_payload": request.original_payload,
    }


@router.post("/approvals/{request_id}/decide")
async def decide_approval(
    request_id: str,
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Record the human decision. On approval, units move available -> reserved.

    Deployment (``reserved -> deployed``) is a separate, explicit action, so
    approving a plan never moves a vehicle by itself.
    """
    service = PlanService(session)
    try:
        result = await service.decide(
            request_id,
            decision=str(payload.get("decision", "")),
            decided_by=actor["user"],
            notes=payload.get("notes"),
            modified_payload=payload.get("modified_payload"),
            reassessment_reason=payload.get("reassessment_reason"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    await hub.broadcast(
        "alerts",
        {"type": "plan_decision", **result, "decided_by": actor["user"]},
    )
    return result


@router.get("/actions/board")
async def action_board(
    session: AsyncSession = Depends(get_session),
    plan_id: str | None = Query(default=None),
) -> dict[str, Any]:
    stmt = select(Action).order_by(Action.created_at.desc())
    if plan_id:
        stmt = stmt.where(Action.plan_id == plan_id)
    actions = list(await session.scalars(stmt.limit(200)))
    return {
        "total": len(actions),
        "items": [
            {
                "action_id": a.action_id,
                "plan_id": a.plan_id,
                "incident_id": a.incident_id,
                "resource_id": a.resource_id,
                "action_type": a.action_type,
                "description": a.description,
                "priority": a.priority,
                "status": a.status.value,
                "assigned_to": a.assigned_to,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "completion_time": (
                    a.completion_time.isoformat() if a.completion_time else None
                ),
                "remarks": a.remarks,
            }
            for a in actions
        ],
    }


@router.post("/actions/{action_id}/start")
async def start_action(
    action_id: str,
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Begin an approved action; this is where a unit actually deploys."""
    try:
        action = await PlanService(session).start_action(
            action_id, actor=actor["user"], resource_id=payload.get("resource_id")
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    await hub.broadcast(
        "alerts",
        {
            "type": "action_started",
            "action_id": action.action_id,
            "resource_id": action.resource_id,
            "incident_id": action.incident_id,
            "started_by": actor["user"],
        },
    )
    return {
        "action_id": action.action_id,
        "status": action.status.value,
        "resource_id": action.resource_id,
        "assigned_to": action.assigned_to,
        "start_time": action.start_time.isoformat() if action.start_time else None,
    }


@router.post("/actions/{action_id}/complete")
async def complete_action(
    action_id: str,
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    try:
        action = await PlanService(session).complete_action(
            action_id, actor=actor["user"], remarks=payload.get("remarks")
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    await hub.broadcast(
        "alerts",
        {
            "type": "action_completed",
            "action_id": action.action_id,
            "resource_id": action.resource_id,
            "completed_by": actor["user"],
        },
    )
    return {
        "action_id": action.action_id,
        "status": action.status.value,
        "completion_time": (
            action.completion_time.isoformat() if action.completion_time else None
        ),
    }