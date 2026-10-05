"""Response plan, allocation, approval, action, alert and report schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    ActionStatus,
    AlertSeverity,
    AlertType,
    AllocationStatus,
    ApprovalStatus,
    DeploymentStatus,
    FactStatus,
    PlanStatus,
    ResourceType,
    RouteStatus,
    SeverityLevel,
)
from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Allocations & plans (Requirements 8, 11)
# ---------------------------------------------------------------------------


class ShortageItem(BaseModel):
    """Deterministic resource deficit (never resolved by inventing stock)."""

    resource_type: ResourceType
    label: str
    required: int
    available: int
    deficit: int
    unit: str = "units"
    note: str


class AllocationOut(BaseModel):
    allocation_id: str | None = None
    incident_id: str
    resource_id: str
    resource_type: ResourceType | None = None
    resource_label: str | None = None
    status: AllocationStatus = AllocationStatus.PROPOSED
    rationale: str
    match_factors: dict[str, Any] = Field(default_factory=dict)
    distance_km: float | None = None
    travel_minutes: float | None = None
    route_status: RouteStatus | None = None
    route_verified: bool = False
    capacity_contribution: int = 0
    coverage_gap: int = 0
    requires_commander_approval: bool = True
    constraints: list[str] = Field(default_factory=list)


class PlanCreate(BaseModel):
    title: str | None = None
    trigger: str = "manual"
    incident_ids: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    plan_id: str
    status: PlanStatus
    title: str
    trigger: str
    summary: str | None
    incident_ids: list[str]
    allocations: list[AllocationOut]
    shortages: list[ShortageItem]
    alternatives: list[str]
    unresolved_issues: list[str]
    reviewer_findings: dict[str, Any] | None = None
    weather_snapshot: dict[str, Any] | None = None
    assumptions: list[str]
    reasoning_trace: dict[str, Any] | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Human-in-the-loop (Requirement 12)
# ---------------------------------------------------------------------------


class ApprovalCreate(BaseModel):
    plan_id: str | None = None
    incident_id: str | None = None
    action_id: str | None = None
    requested_from: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionRequest(BaseModel):
    """Coordinator decision.

    ``decision`` must be one of approve/reject/modify/request_reassessment.
    AI never self-approves; an authorised coordinator must supply their name.
    """

    decision: str
    decided_by: str = Field(min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=4000)
    modifications: dict[str, Any] | None = Field(
        default=None,
        description="Modified allocation payload used with decision='modify'.",
    )

    @property
    def normalised_decision(self) -> str:
        return self.decision.strip().lower().replace(" ", "_")


class ApprovalResponse(ORMModel):
    request_id: str
    plan_id: str | None
    incident_id: str | None
    action_id: str | None
    status: ApprovalStatus
    requested_from: str
    decision: str | None
    decided_by: str | None
    decided_at: datetime | None
    original_payload: dict[str, Any]
    modified_payload: dict[str, Any] | None
    reassessment_reason: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Actions (Requirement 13)
# ---------------------------------------------------------------------------


class ActionCreate(BaseModel):
    incident_id: str | None = None
    plan_id: str | None = None
    resource_id: str | None = None
    action_type: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    priority: str = "moderate"
    assigned_to: str | None = None
    start_time: datetime | None = None
    due_at: datetime | None = None
    remarks: str | None = None


class ActionUpdate(BaseModel):
    status: ActionStatus | None = None
    assigned_to: str | None = None
    start_time: datetime | None = None
    completion_time: datetime | None = None
    remarks: str | None = None


class ActionResponse(ORMModel):
    action_id: str
    incident_id: str | None
    plan_id: str | None
    resource_id: str | None
    action_type: str
    description: str
    priority: str
    status: ActionStatus
    assigned_to: str | None
    approved_by: str | None
    approved_at: datetime | None
    start_time: datetime | None
    completion_time: datetime | None
    due_at: datetime | None
    remarks: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Alerts (Requirement 14)
# ---------------------------------------------------------------------------


class AlertResponse(ORMModel):
    alert_id: str
    incident_id: str | None
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    context: dict[str, Any] | None
    is_active: bool
    acknowledged: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Situation report (Requirement 16)
# ---------------------------------------------------------------------------


class ReportGenerateRequest(BaseModel):
    title: str | None = None
    generated_by: str = "system"
    format: str = "pdf"  # pdf | json
    include_actions: bool = True
    include_pending_approvals: bool = True


class SituationReportPayload(BaseModel):
    """Full structured situation report, persisted for audit."""

    report_id: str
    title: str
    generated_by: str
    generated_at: datetime
    situation: dict[str, Any]
    uncertainties: list[str] = Field(default_factory=list)
    disclaimer: str


class SituationReportSummary(ORMModel):
    report_id: str
    title: str
    generated_by: str
    generated_at: datetime


# ---------------------------------------------------------------------------
# Dashboard (Requirement 15)
# ---------------------------------------------------------------------------


class SeverityBreakdown(BaseModel):
    severity_level: SeverityLevel
    count: int
    incident_ids: list[str]


class DashboardResponse(BaseModel):
    generated_at: datetime
    active_incidents: int
    critical_incidents: int
    people_reported_affected: int
    people_with_verified_priority: int
    incidents_by_severity: list[SeverityBreakdown]
    resources_total: int
    resources_available: int
    resources_deployed: int
    resources_reserved: int
    resource_shortages: list[ShortageItem]
    shelters_total: int
    shelter_capacity: int
    shelter_occupancy: int
    shelter_available: int
    shelter_near_capacity: list[dict[str, Any]]
    blocked_routes: list[dict[str, Any]]
    unverified_routes: list[dict[str, Any]]
    active_alerts: list[AlertResponse]
    pending_approvals: int
    recent_changes: list[dict[str, Any]]
    open_actions: int
    overdue_actions: int
    duplicate_reports: int
    verification_required: int


class MapFeature(BaseModel):
    """GeoJSON-style feature consumed directly by Leaflet."""

    type: str = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


class MapViewResponse(BaseModel):
    features: list[MapFeature]
    center: dict[str, float] = Field(default_factory=lambda: {"lat": 0.0, "lng": 0.0})
    generated_at: datetime


class ReplanTrigger(BaseModel):
    """Input for a dynamic replanning request (Requirement 10)."""

    road_id: str | None = None
    incident_id: str | None = None
    new_status: RouteStatus | None = None
    blocked_reason: str | None = None
    fact_status: FactStatus = FactStatus.KNOWN
    reported_by: str = "field_report"
    weather_worsening: bool = False


class SimulationModeState(BaseModel):
    weather: bool
    routing: bool
    geocoding: bool
    llm: bool
    llm_provider: str