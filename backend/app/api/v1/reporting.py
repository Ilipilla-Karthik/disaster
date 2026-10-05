"""Dashboard, map data, situation reports and the audit trail (Requirement 12)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, require_commander
from app.models.models import Action, AgentRunLog, ApprovalRequest, PlanAllocation
from app.services.reporting_service import DashboardService, ReportingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reporting"])


@router.get("/dashboard")
async def dashboard(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    service = DashboardService(session)
    return {
        "counts": await service.counts(),
        "recent_incidents": [
            {
                "incident_id": i.incident_id,
                "incident_type": i.incident_type.value,
                "severity_level": i.severity_level.value,
                "priority_score": i.priority_score,
                "location": i.location,
                "verification_status": i.verification_status.value,
                "is_duplicate": i.is_duplicate,
                "reported_at": i.reported_at.isoformat() if i.reported_at else None,
            }
            for i in await service.recent_incidents(10)
        ],
        "active_alerts": [
            {
                "alert_id": a.alert_id,
                "alert_type": a.alert_type.value,
                "severity": a.severity.value,
                "message": a.message,
                "incident_id": a.incident_id,
                "acknowledged": a.acknowledged,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in await service.active_alerts(10)
        ],
    }


@router.get("/map")
async def map_features(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """GeoJSON for the operator map, with provenance on every feature."""
    data = await ReportingService(session).map_features()
    return {
        **data,
        "legend": {
            "known": "Authoritative report",
            "inferred": "Agent hypothesis - not a fact",
            "unverified": "Not yet confirmed",
            "unknown": "No information on file",
        },
    }


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def generate_report(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    service = ReportingService(session)
    report = await service.build_report(generated_by=actor["user"])
    if payload.get("persist", True):
        row = await service.persist_report(report, generated_by=actor["user"])
        return {
            "report_id": row.report_id,
            "title": row.title,
            "generated_by": row.generated_by,
            "created_at": row.generated_at.isoformat() if row.generated_at else None,
            "content": row.situation,
            "uncertainties": row.uncertainties,
        }
    return report


@router.get("/reports")
async def list_reports(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, le=100),
) -> dict[str, Any]:
    reports = await ReportingService(session).list_reports(limit=limit)
    return {
        "total": len(reports),
        "items": [
            {
                "report_id": r.report_id,
                "title": r.title,
                "generated_by": r.generated_by,
                "created_at": r.generated_at.isoformat() if r.generated_at else None,
                "incident_count": len((r.situation or {}).get("incidents") or []),
                "uncertainty_count": len(r.uncertainties or []),
            }
            for r in reports
        ],
    }


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    service = ReportingService(session)
    report = await service.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    return {
        "report_id": report.report_id,
        "title": report.title,
        "generated_by": report.generated_by,
        "content": report.situation,
        "uncertainties": report.uncertainties,
        "created_at": report.generated_at.isoformat() if report.generated_at else None,
    }


@router.get("/reports/{report_id}/pdf")
async def download_report_pdf(
    report_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    service = ReportingService(session)
    report = await service.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    pdf = await service.report_pdf(report_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{report_id}.pdf"'
        },
    )


@router.get("/audit")
async def audit_trail(
    session: AsyncSession = Depends(get_session),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    """Human and agent decisions, for accountability.

    Plan approvals are recorded with the officer's name; agent runs are recorded
    with the trace that produced them. Together they answer "who decided this,
    and on what basis".
    """
    from app.models.models import Action, ApprovalRequest, PlanAllocation

    requests = list(await session.scalars(
        select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(limit)
    ))
    agent_logs = list(await session.scalars(
        select(AgentRunLog).order_by(AgentRunLog.created_at.desc()).limit(limit)
    ))
    allocations = list(await session.scalars(
        select(PlanAllocation).order_by(PlanAllocation.created_at.desc()).limit(limit)
    ))
    actions = list(await session.scalars(
        select(Action).order_by(Action.created_at.desc()).limit(limit)
    ))
    if incident_id:
        allocations = [a for a in allocations if a.incident_id == incident_id]
        actions = [a for a in actions if a.incident_id == incident_id]

    return {
        "approvals": [
            {
                "request_id": r.request_id,
                "plan_id": r.plan_id,
                "status": r.status.value,
                "decision": r.decision,
                "requested_from": r.requested_from,
                "decided_by": r.decided_by,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                "reassessment_reason": r.reassessment_reason,
                "was_modified": bool(r.modified_payload),
                "requested_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in requests
        ],
        "agent_runs": [
            {
                "run_id": log.run_id,
                "agent_name": log.agent_name,
                "graph_node": log.graph_node,
                "llm_used": log.llm_used,
                "duration_ms": log.duration_ms,
                "error": log.error,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in agent_logs
        ],
        "allocations": [
            {
                "allocation_id": a.allocation_id,
                "plan_id": a.plan_id,
                "incident_id": a.incident_id,
                "resource_id": a.resource_id,
                "status": a.status.value,
                "capacity_contribution": a.capacity_contribution,
                "coverage_gap": a.coverage_gap,
                "route_status": a.route_status.value if a.route_status else None,
                "route_verified": a.route_verified,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in allocations
        ],
        "actions": [
            {
                "action_id": a.action_id,
                "plan_id": a.plan_id,
                "incident_id": a.incident_id,
                "resource_id": a.resource_id,
                "action_type": a.action_type,
                "status": a.status.value,
                "assigned_to": a.assigned_to,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "completion_time": (
                    a.completion_time.isoformat() if a.completion_time else None
                ),
            }
            for a in actions
        ],
    }