"""Resource registry, shelter and road schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.models.enums import (
    AccessMode,
    DeploymentStatus,
    FactStatus,
    ResourceStatus,
    ResourceType,
    RouteStatus,
    ShelterStatus,
)
from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Resources (Requirements 6)
# ---------------------------------------------------------------------------


class ResourceBase(BaseModel):
    resource_type: ResourceType
    location: str = Field(min_length=1, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    capability: str | None = None
    capability_tags: list[str] = Field(default_factory=list)
    capacity: int = Field(default=1, ge=1)
    access_modes: list[AccessMode] = Field(default_factory=list)


class ResourceCreate(ResourceBase):
    resource_id: str | None = Field(default=None, max_length=32)


class ResourceUpdate(BaseModel):
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capability: str | None = None
    capability_tags: list[str] | None = None
    capacity: int | None = Field(default=None, ge=1)
    status: ResourceStatus | None = None
    expected_availability: datetime | None = None


class ResourceResponse(ORMModel):
    resource_id: str
    resource_type: ResourceType
    location: str
    latitude: float | None
    longitude: float | None
    capability: str | None
    capability_tags: list[str] | None
    capacity: int
    access_modes: list[str] | None
    status: ResourceStatus
    deployment_status: DeploymentStatus
    current_assignment: str | None
    assigned_at: datetime | None
    expected_availability: datetime | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ResourceStatusTransition(BaseModel):
    """Explicit state-machine transition (Requirement 6).

    Valid transitions: AVAILABLE -> RESERVED -> DEPLOYED -> RETURNING -> AVAILABLE.
    """

    status: ResourceStatus
    expected_availability: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Shelters (Requirement 7)
# ---------------------------------------------------------------------------


class ShelterBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(min_length=1, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    capacity: int = Field(ge=0)
    current_occupancy: int = Field(default=0, ge=0)
    operational_status: ShelterStatus = ShelterStatus.OPERATIONAL
    is_accessible: bool = True
    medical_support: bool = False
    contact_info: str | None = None


class ShelterCreate(ShelterBase):
    shelter_id: str | None = Field(default=None, max_length=32)


class ShelterUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capacity: int | None = Field(default=None, ge=0)
    current_occupancy: int | None = Field(default=None, ge=0)
    operational_status: ShelterStatus | None = None
    is_accessible: bool | None = None
    medical_support: bool | None = None
    contact_info: str | None = None


class OccupancyChange(BaseModel):
    """Deterministic occupancy delta. ``change`` may be negative on discharge."""

    change: int
    reason: str | None = Field(default=None, max_length=500)


class ShelterResponse(ORMModel):
    shelter_id: str
    name: str
    location: str
    latitude: float | None
    longitude: float | None
    capacity: int
    current_occupancy: int
    operational_status: ShelterStatus
    is_accessible: bool
    medical_support: bool
    contact_info: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_capacity(self) -> int:
        return max(0, self.capacity - self.current_occupancy)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def utilization_pct(self) -> float:
        if not self.capacity:
            return 0.0
        return round(self.current_occupancy / self.capacity * 100.0, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_near_capacity(self) -> bool:
        return self.capacity > 0 and self.utilization_pct >= 85.0


# ---------------------------------------------------------------------------
# Roads / routes (Requirement 9)
# ---------------------------------------------------------------------------


class RoadBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str | None = None
    origin_zone: str | None = None
    destination_zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    length_km: float | None = Field(default=None, ge=0)
    access_mode: AccessMode = AccessMode.ROAD
    # Provenance is mandatory: callers must declare whether a status is an
    # authoritative report or an inference.
    fact_status: FactStatus = FactStatus.UNVERIFIED
    reported_by: str | None = None


class RoadCreate(RoadBase):
    road_id: str | None = None


class RoadUpdate(BaseModel):
    name: str | None = None
    status: RouteStatus | None = None
    fact_status: FactStatus | None = None
    blocked_reason: str | None = None
    reported_by: str | None = None
    length_km: float | None = Field(default=None, ge=0)


class RoadResponse(ORMModel):
    road_id: str
    name: str
    location: str | None
    origin_zone: str | None
    destination_zone: str | None
    latitude: float | None
    longitude: float | None
    length_km: float | None
    status: RouteStatus
    fact_status: FactStatus
    blocked_reason: str | None
    reported_by: str | None
    reported_at: datetime | None
    access_mode: AccessMode
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_verified(self) -> bool:
        return self.fact_status == FactStatus.KNOWN

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verification_note(self) -> str | None:
        """Explicit note when routing information is unavailable."""
        if self.status == RouteStatus.UNKNOWN:
            return "Route verification required - no authoritative condition report on file."
        if self.fact_status == FactStatus.INFERRED:
            return (
                "INFERRED condition - not an authoritative road closure. "
                "Field verification required before operational use."
            )
        return None


class RouteEstimate(BaseModel):
    """Result of a routing query (OSRM or documented fallback)."""

    origin: str
    destination: str
    distance_km: float | None
    travel_minutes: float | None
    source: str  # "osrm" | "fallback_estimate"
    verified: bool
    note: str | None = None