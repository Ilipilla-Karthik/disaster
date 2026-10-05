"""Incident submission, listing and verification (Requirements 2, 3).

Submitting an incident runs the intake agent, stores the canonical record, and
returns what could *not* be determined. Nothing is guessed to fill a gap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.intake_agent import IncidentIntakeAgent
from app.api.deps import get_actor, get_session
from app.models.enums import (
    AlertSeverity,
    AlertType,
    SeverityLevel,
    VerificationStatus,
)
from app.models.models import Incident
from app.orchestration.state import new_state
from app.schemas.incident import IncidentCreate, IncidentUpdate
from app.services.alert_service import AlertService
from app.services.normalizer import IncidentNormalizer, next_incident_id
from app.services.reporting_service import ReportingService
from app.services.resource_service import ResourceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_incident(
    payload: IncidentCreate,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(get_actor),
) -> dict[str, Any]:
    """Accept a structured or unstructured incident report.

    ``raw_text`` is extracted by the intake agent (LLM when configured, keyword
    rules otherwise). Structured fields supplied in the body always win over
    extracted ones, and the raw text is retained for audit.
    """
    agent = IncidentIntakeAgent()
    # exclude_unset matters: schema defaults (people_reported_affected=0, a null
    # location) are not evidence. Passing them through would let a placeholder
    # default overwrite what the extractor actually found in the report text.
    body = payload.model_dump(mode="json", exclude_unset=True)
    body.setdefault("source", actor["user"])

    # Duplicate detection compares the new report against what is already on
    # file. Without this the agent has nothing to compare against and every
    # report looks unique.
    known = list(
        await session.scalars(
            select(Incident).where(
                Incident.reported_at
                >= datetime.now(timezone.utc) - timedelta(hours=6)
            )
        )
    )
    state = new_state(
        incident_payloads=[body],
        incidents=[
            {
                "incident_id": i.incident_id,
                "incident_type": i.incident_type.value,
                "location": i.location,
                "latitude": i.latitude,
                "longitude": i.longitude,
                "description": i.description,
                "raw_report": i.raw_report,
                "people_reported_affected": i.people_reported_affected,
                "reported_at": i.reported_at.isoformat() if i.reported_at else None,
                "is_duplicate": i.is_duplicate,
                "duplicate_of": i.duplicate_of,
            }
            for i in known
        ],
    )
    try:
        update = await agent.execute(state)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Intake failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Intake agent could not process the report: {exc}",
        ) from exc

    record = (update.get("normalized_incidents") or [None])[0]
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The report produced no incident record.",
        )

    incident_id = await next_incident_id(session)
    incident = Incident(
        incident_id=incident_id,
        incident_type=record.get("incident_type"),
        location=record.get("location") or "Unknown",
        latitude=record.get("latitude"),
        longitude=record.get("longitude"),
        description=record.get("description"),
        people_reported_affected=int(record.get("people_reported_affected") or 0),
        infrastructure_issue=record.get("infrastructure_issue"),
        assistance_requested=record.get("assistance_requested"),
        raw_report=record.get("raw_text") or payload.raw_text,
        source=record.get("source") or actor["user"],
        source_type=record.get("source_type"),
        confidence=record.get("confidence"),
        verification_status=VerificationStatus.PENDING,
        missing_fields=record.get("missing_fields") or [],
        is_duplicate=bool(record.get("is_duplicate")),
        duplicate_of=record.get("duplicate_of"),
        duplicate_similarity=record.get("duplicate_similarity"),
        linked_incident_ids=record.get("linked_incident_ids") or [],
        reported_at=record.get("reported_at") or datetime.now(timezone.utc),
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    # Duplicate link: keep both rows, flag the newer one. The intake agent pops
    # the links off the record into its own output, so read them from there -
    # reading the record silently yields nothing and the review alert is lost.
    links = (update.get("duplicate_links") or []) + (record.get("duplicate_links") or [])
    if links and incident.duplicate_of:
        parent = await session.scalar(
            select(Incident).where(Incident.incident_id == incident.duplicate_of)
        )
        if parent is not None:
            merged = list(parent.linked_incident_ids or [])
            if incident_id not in merged:
                merged.append(incident_id)
            parent.linked_incident_ids = merged
        await AlertService(session).raise_alert(
            AlertType.DUPLICATE_DETECTED,
            f"{incident_id} may be a duplicate of {incident.duplicate_of} "
            f"(similarity {incident.duplicate_similarity}). Linked for review - not "
            "merged or deleted.",
            severity=AlertSeverity.WARNING,
            incident_id=incident_id,
            context={"duplicate_of": incident.duplicate_of},
        )
        # raise_alert() only flushes, and nothing after it in this handler
        # writes. Without this commit the review flag is lost and a flagged
        # duplicate stays invisible outside the incident record.
        await session.commit()
        await session.refresh(incident)

    result = await ReportingService(session).incident_payload(incident)
    result["intake"] = {
        "extraction_method": record.get("extraction_method"),
        "missing_fields": record.get("missing_fields"),
        "verification_required": record.get("verification_required"),
        "duplicate_links": links,
        "notes": update.get("intake_output", {}).get("notes", []),
        "geocode": record.get("geocode"),
    }
    logger.info(
        "Incident %s submitted (%s, verification_required=%s)",
        incident_id, record.get("extraction_method"), record.get("verification_required"),
    )
    return result


@router.get("")
async def list_incidents(
    session: AsyncSession = Depends(get_session),
    include_duplicates: bool = Query(default=True),
    verified: bool | None = Query(default=None),
    severity: SeverityLevel | None = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    stmt = select(Incident).order_by(Incident.priority_score.desc(), Incident.reported_at.desc())
    if not include_duplicates:
        stmt = stmt.where(Incident.is_duplicate.is_(False))
    if verified is not None:
        stmt = stmt.where(
            Incident.verification_status
            == (VerificationStatus.VERIFIED if verified else VerificationStatus.PENDING)
        )
    if severity:
        stmt = stmt.where(Incident.severity_level == severity)
    rows = list(await session.scalars(stmt.limit(limit).offset(offset)))
    reporter = ReportingService(session)
    return {
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "items": [await reporter.incident_payload(i) for i in rows],
    }


@router.get("/map")
async def map_features(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await ReportingService(session).map_features()


@router.get("/{incident_id}")
async def get_incident(
    incident_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    detail = await ReportingService(session).incident_detail(incident_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return detail


@router.patch("/{incident_id}")
async def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(get_actor),
) -> dict[str, Any]:
    """Human correction. Supplying the previously missing fields is what allows
    an incident to move out of *Verification Required*."""
    incident = await session.scalar(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")

    data = payload.model_dump(exclude_unset=True)
    notes = data.pop("notes", None)
    previous = incident.verification_status

    for key, value in data.items():
        if value is not None:
            setattr(incident, key, value)

    normalizer = IncidentNormalizer()
    incident.missing_fields = normalizer.missing_fields(
        {
            "incident_type": incident.incident_type,
            "location": incident.location,
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "people_reported_affected": incident.people_reported_affected,
            "description": incident.description,
        }
    )
    if payload.verification_status == VerificationStatus.VERIFIED and incident.missing_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Cannot mark verified while required fields are missing.",
                "missing_fields": incident.missing_fields,
            },
        )
    if notes:
        incident.uncertainty_notes = notes
    if payload.verification_status:
        incident.verification_status = payload.verification_status
        if payload.verification_status == VerificationStatus.VERIFIED:
            incident.last_reassessed_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(incident)

    if (
        previous != VerificationStatus.VERIFIED
        and incident.verification_status == VerificationStatus.VERIFIED
    ):
        await AlertService(session).clear_incident_alerts(
            incident_id, f"Incident verified by {actor['user']}"
        )
        logger.info("Incident %s verified by %s", incident_id, actor["user"])

    return await ReportingService(session).incident_detail(incident_id)


@router.get("/{incident_id}/duplicates")
async def duplicate_candidates(
    incident_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    incident = await session.scalar(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    others = list(
        await session.scalars(
            select(Incident).where(Incident.incident_id != incident_id)
        )
    )
    from app.services.duplicate_detector import DuplicateDetector

    matches = DuplicateDetector().find_duplicates(
        {
            "incident_id": incident_id,
            "incident_type": incident.incident_type.value,
            "location": incident.location,
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "description": incident.description,
            "reported_at": incident.reported_at,
        },
        others,
    )
    return {
        "incident_id": incident_id,
        "candidates": [m.to_dict() for m in matches],
        "policy": (
            "Candidates are surfaced for human review only. Nothing is merged or "
            "deleted automatically."
        ),
    }