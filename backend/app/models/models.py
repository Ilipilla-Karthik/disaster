"""SQLAlchemy ORM models - the persistent operational picture.

Design notes
------------
* Enum columns are stored as their lowercase *values* (not Python member names)
  and rendered as ``VARCHAR + CHECK`` so the same schema works on PostgreSQL and
  SQLite without Postgres-specific enum migrations.
* ``Road.fact_status`` encodes provenance: a closure reported by an authority is
  ``KNOWN``; something an agent inferred is ``INFERRED``. The specification
  requires these never be presented as equivalent facts.
* Tables carrying decision provenance: ``ResponsePlan`` (allocation engine
  output), ``ApprovalRequest`` (human-in-the-loop gate) and
  ``SituationReport`` (snapshot exports).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import (
    AccessMode,
    ActionStatus,
    AlertSeverity,
    AlertType,
    AllocationStatus,
    ApprovalStatus,
    Confidence,
    DeploymentStatus,
    FactStatus,
    IncidentType,
    PlanStatus,
    ResourceStatus,
    ResourceType,
    RouteStatus,
    SeverityLevel,
    ShelterStatus,
    SourceType,
    VerificationStatus,
)

EnumCol = SQLEnum


def _values(enum_cls):
    """Store enum *values* (lowercase strings) rather than member names."""
    return [m.value for m in enum_cls]


def enum_col(enum_cls, **kwargs):
    return SQLEnum(
        enum_cls,
        values_callable=_values,
        native_enum=False,
        length=64,
        validate_strings=True,
        **kwargs,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class Incident(Base, TimestampMixin):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    incident_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    incident_type: Mapped[IncidentType] = mapped_column(enum_col(IncidentType), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    description: Mapped[Optional[str]] = mapped_column(Text)
    people_reported_affected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    infrastructure_issue: Mapped[Optional[str]] = mapped_column(String(128))
    assistance_requested: Mapped[Optional[str]] = mapped_column(String(128))
    # Raw supporting information / original report text (audit trail).
    raw_report: Mapped[Optional[str]] = mapped_column(Text)

    source: Mapped[Optional[str]] = mapped_column(String(64))
    source_type: Mapped[Optional[SourceType]] = mapped_column(enum_col(SourceType))
    confidence: Mapped[Confidence] = mapped_column(
        enum_col(Confidence), default=Confidence.MEDIUM, nullable=False
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_col(VerificationStatus), default=VerificationStatus.PENDING, nullable=False
    )

    severity_level: Mapped[SeverityLevel] = mapped_column(
        enum_col(SeverityLevel), default=SeverityLevel.VERIFICATION_REQUIRED, nullable=False
    )
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    priority_factors: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # Fields the intake agent could not resolve -> surfaced as "Verification
    # Required" rather than guessed.
    missing_fields: Mapped[Optional[list]] = mapped_column(JSON)
    uncertainty_notes: Mapped[Optional[str]] = mapped_column(Text)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duplicate_of: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("incidents.incident_id", use_alter=True)
    )
    duplicate_similarity: Mapped[Optional[float]] = mapped_column(Float)
    linked_incident_ids: Mapped[Optional[list]] = mapped_column(JSON)

    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_reassessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    escalation_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    duplicate_incidents: Mapped[list["Incident"]] = relationship(
        "Incident",
        remote_side=[incident_id],
        backref="duplicate_parent",
        foreign_keys=[duplicate_of],
    )

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# ---------------------------------------------------------------------------
# Resources / shelters / roads
# ---------------------------------------------------------------------------


class Resource(Base, TimestampMixin):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    resource_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    resource_type: Mapped[ResourceType] = mapped_column(enum_col(ResourceType), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    capability: Mapped[Optional[str]] = mapped_column(Text)
    # Discrete capability tags used for deterministic matching, e.g.
    # ["water_rescue", "evacuation", "night_ops"].
    capability_tags: Mapped[Optional[list]] = mapped_column(JSON)
    capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Restricts deployment to certain access modes (water rescue cannot use roads).
    access_modes: Mapped[Optional[list]] = mapped_column(JSON)

    status: Mapped[ResourceStatus] = mapped_column(
        enum_col(ResourceStatus), default=ResourceStatus.AVAILABLE, nullable=False
    )
    deployment_status: Mapped[DeploymentStatus] = mapped_column(
        enum_col(DeploymentStatus), default=DeploymentStatus.RECOMMENDED, nullable=False
    )

    current_assignment: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("incidents.incident_id", use_alter=True)
    )
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expected_availability: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    @property
    def is_available(self) -> bool:
        return self.status == ResourceStatus.AVAILABLE


class Shelter(Base, TimestampMixin):
    __tablename__ = "shelters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    shelter_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)

    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    current_occupancy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    operational_status: Mapped[ShelterStatus] = mapped_column(
        enum_col(ShelterStatus), default=ShelterStatus.OPERATIONAL, nullable=False
    )
    # Medical / accessibility support flags.
    is_accessible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    medical_support: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contact_info: Mapped[Optional[str]] = mapped_column(String(128))

    @property
    def available_capacity(self) -> int:
        """Deterministic remaining capacity (never negative)."""
        return max(0, self.capacity - self.current_occupancy)

    @property
    def utilization_pct(self) -> float:
        if not self.capacity:
            return 0.0
        return round(self.current_occupancy / self.capacity * 100.0, 2)


class Road(Base, TimestampMixin):
    __tablename__ = "roads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    road_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255))
    origin_zone: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    destination_zone: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    length_km: Mapped[Optional[float]] = mapped_column(Float)

    status: Mapped[RouteStatus] = mapped_column(
        enum_col(RouteStatus), default=RouteStatus.UNKNOWN, nullable=False
    )
    # Provenance guard: KNOWN closure vs INFERRED accessibility problem.
    fact_status: Mapped[FactStatus] = mapped_column(
        enum_col(FactStatus), default=FactStatus.UNKNOWN, nullable=False
    )
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text)
    reported_by: Mapped[Optional[str]] = mapped_column(String(128))
    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    access_mode: Mapped[AccessMode] = mapped_column(
        enum_col(AccessMode), default=AccessMode.ROAD, nullable=False
    )


# ---------------------------------------------------------------------------
# Decision artefacts
# ---------------------------------------------------------------------------


class ResponsePlan(Base, TimestampMixin):
    """A generated (never auto-executed) response plan."""

    __tablename__ = "response_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    status: Mapped[PlanStatus] = mapped_column(
        enum_col(PlanStatus), default=PlanStatus.DRAFT, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255))
    trigger: Mapped[str] = mapped_column(String(64))  # e.g. initial_assessment / replan:road_closed
    summary: Mapped[Optional[str]] = mapped_column(Text)

    incident_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allocations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    shortages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    alternatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unresolved_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reviewer_findings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    weather_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)

    # Full LangGraph state for explainability / audit.
    reasoning_trace: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    assumptions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    superseded_by: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("response_plans.plan_id", use_alter=True)
    )

    approved_by: Mapped[Optional[str]] = mapped_column(String(128))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    allocations_rel: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation", back_populates="plan", cascade="all, delete-orphan"
    )


class PlanAllocation(Base, TimestampMixin):
    """One resource -> one incident recommendation inside a plan."""

    __tablename__ = "plan_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    allocation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    plan_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("response_plans.plan_id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[str] = mapped_column(String(32), ForeignKey("incidents.incident_id"), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), ForeignKey("resources.resource_id"), nullable=False)

    status: Mapped[AllocationStatus] = mapped_column(
        enum_col(AllocationStatus), default=AllocationStatus.PROPOSED, nullable=False
    )
    # Required capability tags that justify the match.
    rationale: Mapped[str] = mapped_column(Text)
    match_factors: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    distance_km: Mapped[Optional[float]] = mapped_column(Float)
    travel_minutes: Mapped[Optional[float]] = mapped_column(Float)
    route_status: Mapped[Optional[RouteStatus]] = mapped_column(enum_col(RouteStatus))
    route_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capacity_contribution: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_gap: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plan: Mapped["ResponsePlan"] = relationship("ResponsePlan", back_populates="allocations_rel")


class ApprovalRequest(Base, TimestampMixin):
    """Human-in-the-loop gate. Nothing dispatches without an APPROVED record."""

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    plan_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("response_plans.plan_id", use_alter=True)
    )
    incident_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("incidents.incident_id"))
    action_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("actions.action_id"))

    status: Mapped[ApprovalStatus] = mapped_column(
        enum_col(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False
    )
    requested_from: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[Optional[str]] = mapped_column(Text)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    original_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    modified_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    # "reassessment_requested" resolves back into a fresh graph run.
    reassessment_reason: Mapped[Optional[str]] = mapped_column(Text)


class Action(Base, TimestampMixin):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    action_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    incident_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("incidents.incident_id"))
    plan_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("response_plans.plan_id"))
    resource_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("resources.resource_id"))

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="moderate", nullable=False)

    status: Mapped[ActionStatus] = mapped_column(
        enum_col(ActionStatus), default=ActionStatus.PENDING, nullable=False
    )
    assigned_to: Mapped[Optional[str]] = mapped_column(String(128))
    approved_by: Mapped[Optional[str]] = mapped_column(String(128))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completion_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    remarks: Mapped[Optional[str]] = mapped_column(Text)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    alert_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    incident_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("incidents.incident_id"))
    alert_type: Mapped[AlertType] = mapped_column(enum_col(AlertType), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        enum_col(AlertSeverity), default=AlertSeverity.INFO, nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Machine-readable payload so the UI can deep-link to the cause.
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(128))
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SituationReport(Base):
    """Persisted snapshot of a situation report (also downloadable as PDF)."""

    __tablename__ = "situation_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    situation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    uncertainties: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRunLog(Base):
    """Per-node execution log for explainability (which agent ran, when, why)."""

    __tablename__ = "agent_run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_node: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    output_digest: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )