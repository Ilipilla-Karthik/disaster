"""Situation reporting and dashboard aggregation.

Assembles the read models the API and UI consume: incident cards, the live
dashboard, map features, and the situation report (JSON and PDF).

Everything is assembled from persisted state - the report never re-runs the
agents, so a report always matches the plan that was approved.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AlertSeverity,
    IncidentType,
    PlanStatus,
    ResourceStatus,
    RouteStatus,
    SeverityLevel,
    VerificationStatus,
)
from app.models.models import (
    Action,
    Alert,
    Incident,
    Resource,
    Road,
    Shelter,
    SituationReport,
)
from app.services.geo import haversine_km
from app.tools.pdf_tool import build_situation_report_pdf

logger = logging.getLogger(__name__)

__all__ = ["ReportingService", "DashboardService"]


class DashboardService:
    """Read-only aggregation for the operations dashboard."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def counts(self) -> dict[str, int]:
        def _count(model: Any, *where: Any) -> Any:
            return func.count(model.id)

        total = await self.session.scalar(select(func.count()).select_from(Incident))
        duplicates = await self.session.scalar(
            select(func.count()).select_from(Incident).where(Incident.is_duplicate.is_(True))
        )
        verification = await self.session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.verification_status != VerificationStatus.VERIFIED)
        )
        by_severity: dict[str, int] = {}
        for level, count in (
            await self.session.execute(
                select(Incident.severity_level, func.count()).group_by(Incident.severity_level)
            )
        ).all():
            by_severity[level.value if hasattr(level, "value") else str(level)] = count

        resources_total = await self.session.scalar(
            select(func.count()).select_from(Resource)
        )
        available = await self.session.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.status == ResourceStatus.AVAILABLE)
        )
        deployed = await self.session.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.status == ResourceStatus.DEPLOYED)
        )
        alerts_active = await self.session.scalar(
            select(func.count()).select_from(Alert).where(Alert.is_active.is_(True))
        )
        shelters = list(await self.session.scalars(select(Shelter)))
        capacity = sum(int(s.capacity) for s in shelters)
        occupancy = sum(int(s.current_occupancy) for s in shelters)
        roads_closed = await self.session.scalar(
            select(func.count())
            .select_from(Road)
            .where(Road.status.in_([RouteStatus.CLOSED.value, RouteStatus.IMPASSABLE.value]))
        )
        return {
            "incidents_total": int(total or 0),
            "incidents_duplicate": int(duplicates or 0),
            "incidents_needing_verification": int(verification or 0),
            "incidents_by_severity": by_severity,
            "resources_total": int(resources_total or 0),
            "resources_available": int(available or 0),
            "resources_deployed": int(deployed or 0),
            "alerts_active": int(alerts_active or 0),
            "shelter_capacity": capacity,
            "shelter_occupancy": occupancy,
            "shelter_available": max(0, capacity - occupancy),
            "roads_closed": int(roads_closed or 0),
        }

    async def recent_incidents(self, limit: int = 20) -> list[Incident]:
        return list(
            await self.session.scalars(
                select(Incident).order_by(Incident.reported_at.desc()).limit(limit)
            )
        )

    async def active_alerts(self, limit: int = 20) -> list[Alert]:
        return list(
            await self.session.scalars(
                select(Alert)
                .where(Alert.is_active.is_(True))
                .order_by(Alert.created_at.desc())
                .limit(limit)
            )
        )


class ReportingService:
    """Situation report assembly, persistence and PDF export."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.dashboard = DashboardService(session)

    # -- incident cards ------------------------------------------------------

    async def incident_payload(self, incident: Incident) -> dict[str, Any]:
        allocations = list(
            await self.session.scalars(
                select(Action).where(Action.incident_id == incident.incident_id)
            )
        )
        return {
            "incident_id": incident.incident_id,
            "incident_type": incident.incident_type.value,
            "location": incident.location,
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "description": incident.description,
            "people_reported_affected": incident.people_reported_affected,
            "severity": incident.severity_level.value,
            "priority_score": incident.priority_score,
            "priority_factors": incident.priority_factors,
            "verification_status": incident.verification_status.value,
            "missing_fields": incident.missing_fields or [],
            "uncertainty_notes": incident.uncertainty_notes,
            "is_duplicate": incident.is_duplicate,
            "duplicate_of": incident.duplicate_of,
            "duplicate_similarity": incident.duplicate_similarity,
            "linked_incident_ids": incident.linked_incident_ids or [],
            "source": incident.source,
            "reported_at": incident.reported_at.isoformat() if incident.reported_at else None,
            "escalation_flag": incident.escalation_flag,
            "actions": [
                {
                    "action_id": a.action_id,
                    "action_type": a.action_type,
                    "status": a.status.value,
                    "description": a.description,
                }
                for a in allocations
            ],
        }

    async def incident_detail(self, incident_id: str) -> dict[str, Any] | None:
        incident = await self.session.scalar(
            select(Incident).where(Incident.incident_id == incident_id)
        )
        if incident is None:
            return None
        payload = await self.incident_payload(incident)
        payload["raw_report"] = incident.raw_report
        payload["assessment"] = incident.priority_factors
        return payload

    # -- map -----------------------------------------------------------------

    async def map_features(self) -> dict[str, Any]:
        incidents = list(await self.session.scalars(select(Incident)))
        resources = list(await self.session.scalars(select(Resource)))
        shelters = list(await self.session.scalars(select(Shelter)))
        roads = list(await self.session.scalars(select(Road)))

        return {
            "incidents": [
                {
                    "id": i.incident_id,
                    "type": "incident",
                    "incident_type": i.incident_type.value,
                    "latitude": i.latitude,
                    "longitude": i.longitude,
                    "severity": i.severity_level.value,
                    "priority_score": i.priority_score,
                    "label": f"{i.incident_id} - {i.location}",
                    "verification_status": i.verification_status.value,
                    "is_duplicate": i.is_duplicate,
                    "unverified": i.verification_status != VerificationStatus.VERIFIED,
                }
                for i in incidents
            ],
            "resources": [
                {
                    "id": r.resource_id,
                    "type": "resource",
                    "resource_type": r.resource_type.value,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "status": r.status.value,
                    "label": f"{r.resource_id} - {r.resource_type.value}",
                }
                for r in resources
            ],
            "shelters": [
                {
                    "id": s.shelter_id,
                    "type": "shelter",
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "capacity": s.capacity,
                    "occupancy": s.current_occupancy,
                    "available": s.available_capacity,
                    "label": f"{s.shelter_id} - {s.name}",
                }
                for s in shelters
            ],
            "roads": [
                {
                    "id": r.road_id,
                    "type": "road",
                    "name": r.name,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "status": r.status.value,
                    "fact_status": r.fact_status.value,
                    "is_fact": r.fact_status.value == "known",
                    "origin_zone": r.origin_zone,
                    "destination_zone": r.destination_zone,
                    "label": f"{r.road_id} - {r.name} ({r.status.value})",
                }
                for r in roads
            ],
            "legend": {
                "verified": "Verified by an authoritative source",
                "unverified": "Awaiting field verification",
                "known_closure": "Authoritative road closure report",
                "inferred": "Agent inference - not a fact",
            },
        }

    # -- report --------------------------------------------------------------

    async def build_report(self, *, generated_by: str = "system") -> dict[str, Any]:
        counts = await self.dashboard.counts()
        incidents = list(await self.session.scalars(select(Incident)))
        resources = list(await self.session.scalars(select(Resource)))
        shelters = list(await self.session.scalars(select(Shelter)))
        roads = list(await self.session.scalars(select(Road)))
        alerts = await self.dashboard.active_alerts()

        uncertainties: list[str] = []
        for incident in incidents:
            if incident.verification_status != VerificationStatus.VERIFIED:
                uncertainties.append(
                    f"{incident.incident_id}: {incident.verification_status.value} "
                    f"- unverified fields: {', '.join(incident.missing_fields or []) or 'n/a'}"
                )
            if incident.is_duplicate:
                uncertainties.append(
                    f"{incident.incident_id}: possible duplicate of "
                    f"{incident.duplicate_of} (similarity "
                    f"{incident.duplicate_similarity}) - grouped for human review, "
                    "not deleted."
                )
        for road in roads:
            if road.status == RouteStatus.UNKNOWN:
                uncertainties.append(
                    f"{road.road_id}: no authoritative condition report. Unknown is "
                    "not treated as open."
                )
        uncertainties.append(
            "All agent outputs are recommendations requiring authorised human approval."
        )

        situation = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "incidents": [
                await self.incident_payload(i)
                for i in sorted(
                    incidents, key=lambda x: x.priority_score or 0, reverse=True
                )
            ],
            "resources": [
                {
                    "resource_id": r.resource_id,
                    "resource_type": r.resource_type.value,
                    "status": r.status.value,
                    "location": r.location,
                    "capacity": r.capacity,
                    "current_assignment": r.current_assignment,
                    "capability_tags": r.capability_tags or [],
                }
                for r in resources
            ],
            "shelters": [
                {
                    "shelter_id": s.shelter_id,
                    "name": s.name,
                    "capacity": s.capacity,
                    "current_occupancy": s.current_occupancy,
                    "available_capacity": s.available_capacity,
                    "operational_status": s.operational_status.value,
                }
                for s in shelters
            ],
            "roads": [
                {
                    "road_id": r.road_id,
                    "name": r.name,
                    "status": r.status.value,
                    "fact_status": r.fact_status.value,
                    "reported_by": r.reported_by,
                    "origin_zone": r.origin_zone,
                    "destination_zone": r.destination_zone,
                }
                for r in roads
            ],
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "alert_type": a.alert_type.value,
                    "severity": a.severity.value,
                    "message": a.message,
                    "incident_id": a.incident_id,
                }
                for a in alerts
            ],
        }
        return {
            "title": "Multi-Agent Disaster Response Situation Report",
            "generated_by": generated_by,
            "situation": situation,
            "uncertainties": uncertainties,
        }

    async def persist_report(
        self, report: dict[str, Any] | None = None, *, generated_by: str = "system"
    ) -> SituationReport:
        payload = report or await self.build_report(generated_by=generated_by)
        row = SituationReport(
            report_id=f"RPT-{uuid.uuid4().hex[:6].upper()}",
            title=payload["title"],
            generated_by=generated_by,
            situation=payload["situation"],
            uncertainties=payload["uncertainties"],
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def list_reports(self, limit: int = 20) -> list[SituationReport]:
        return list(
            await self.session.scalars(
                select(SituationReport)
                .order_by(SituationReport.generated_at.desc())
                .limit(limit)
            )
        )

    async def get_report(self, report_id: str) -> SituationReport | None:
        return await self.session.scalar(
            select(SituationReport).where(SituationReport.report_id == report_id)
        )

    async def report_pdf(self, report_id: str) -> bytes:
        row = await self.get_report(report_id)
        if row is None:
            raise LookupError(f"Report '{report_id}' not found")
        return build_situation_report_pdf(
            {
                "title": row.title,
                "generated_by": row.generated_by,
                "generated_at": (
                    row.generated_at.isoformat() if row.generated_at else None
                ),
                "report_id": row.report_id,
                "situation": row.situation,
                "uncertainties": row.uncertainties,
            }
        )