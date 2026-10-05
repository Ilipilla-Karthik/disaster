"""Incident intake / query schemas.

``IncidentCreate`` accepts either structured fields or free text (``raw_text``)
so unstructured citizen reports, field updates and sensor events all funnel
through the same normalisation path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import (
    Confidence,
    IncidentType,
    SeverityLevel,
    SourceType,
    VerificationStatus,
)
from app.schemas.common import ORMModel


class IncidentBase(BaseModel):
    incident_type: IncidentType | None = Field(
        default=None,
        description="Optional. Omit for an unstructured report and the intake agent "
        "will extract it, flagging the field as needing verification.",
    )
    location: str | None = Field(
        default=None,
        max_length=255,
        description="Optional. Omit and the intake agent geocodes it; an unresolved "
        "location is recorded as 'Unknown - verification required', never guessed.",
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    people_reported_affected: int = Field(default=0, ge=0, le=1_000_000)
    infrastructure_issue: str | None = Field(default=None, max_length=128)
    assistance_requested: str | None = Field(default=None, max_length=128)
    source: str | None = Field(default=None, max_length=64)
    source_type: SourceType = SourceType.MANUAL
    confidence: Confidence = Confidence.MEDIUM
    reported_at: datetime | None = None


class IncidentCreate(IncidentBase):
    """Registration payload.

    ``raw_text`` is used by the intake agent when the report is unstructured;
    the agent then extracts the structured fields above. When both are supplied
    the structured values win, and ``raw_text`` is retained for audit.
    """

    raw_text: str | None = Field(
        default=None,
        max_length=8000,
        description="Original unstructured report text (emergency call transcript, "
        "citizen message, sensor payload...).",
    )

    @field_validator("people_reported_affected")
    @classmethod
    def _default_people(cls, v: int) -> int:
        return max(0, v)

    @model_validator(mode="after")
    def _needs_something_to_intake(self) -> "IncidentCreate":
        """A report with neither structured fields nor text is not a report."""
        if not self.raw_text and not self.location and not self.incident_type:
            raise ValueError(
                "Provide raw_text (unstructured report) or at least one structured "
                "field (incident_type / location)."
            )
        return self


class IncidentUpdate(BaseModel):
    """Human corrections to an incident (Verification Required resolution)."""

    location: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = None
    people_reported_affected: int | None = Field(default=None, ge=0)
    infrastructure_issue: str | None = None
    assistance_requested: str | None = None
    verification_status: VerificationStatus | None = None
    confidence: Confidence | None = None
    notes: str | None = Field(default=None, max_length=2000)


class PriorityFactor(BaseModel):
    """One transparent contributor to the priority score."""

    factor: str
    label: str
    raw_value: Any = None
    contribution: float
    weight: float
    evidence: str


class PriorityAssessment(ORMModel):
    incident_id: str
    priority_score: float = Field(ge=0, le=100)
    severity_level: SeverityLevel
    factors: list[PriorityFactor] = Field(default_factory=list)
    band_thresholds: dict[str, float] = Field(default_factory=dict)
    escalation_potential: str | None = None
    verification_required_fields: list[str] = Field(default_factory=list)
    rationale: str | None = None
    methodology: str = (
        "Deterministic weighted model: sum of capped factor contributions, "
        "clamped to 0-100. No LLM-generated numbers."
    )


class DuplicateLink(ORMModel):
    incident_id: str
    duplicate_of: str | None = None
    similarity: float | None = None
    matched_on: list[str] = Field(default_factory=list)
    requires_review: bool = True


class IncidentResponse(ORMModel):
    incident_id: str
    incident_type: IncidentType
    location: str
    latitude: float | None
    longitude: float | None
    description: str | None
    people_reported_affected: int
    infrastructure_issue: str | None
    assistance_requested: str | None
    source: str | None
    source_type: SourceType | None
    confidence: Confidence
    verification_status: VerificationStatus
    severity_level: SeverityLevel
    priority_score: float
    priority_factors: dict[str, Any] | None
    missing_fields: list[str] | None
    uncertainty_notes: str | None
    is_duplicate: bool
    duplicate_of: str | None
    duplicate_similarity: float | None
    linked_incident_ids: list[str] | None
    reported_at: datetime
    last_reassessed_at: datetime | None
    escalation_flag: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IncidentDetail(IncidentResponse):
    """Incident plus derived assessment context for the detail page."""

    assessment: PriorityAssessment | None = None
    accessibility: dict[str, Any] | None = None
    weather: dict[str, Any] | None = None
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_candidates: list[DuplicateLink] = Field(default_factory=list)


class IncidentIntakeResult(BaseModel):
    """Result of running the intake pipeline for one report."""

    incident: IncidentResponse
    extracted: dict[str, Any]
    duplicates: list[DuplicateLink] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    verification_required: bool = False
    notes: list[str] = Field(default_factory=list)