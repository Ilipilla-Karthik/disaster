"""Domain enumerations shared by ORM models, schemas and services.

These enums are the controlled vocabulary for the operational picture. Any
value not present here is rejected at the schema boundary rather than being
invented downstream.
"""

from enum import Enum


class IncidentType(str, Enum):
    FLOOD = "flood"
    CYCLONE = "cyclone"
    EARTHQUAKE = "earthquake"
    WILDFIRE = "wildfire"
    LANDSLIDE = "landslide"
    INDUSTRIAL_ACCIDENT = "industrial_accident"
    OTHER = "other"


class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    VERIFICATION_REQUIRED = "verification_required"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FALSE_REPORT = "false_report"
    NEEDS_REVIEW = "needs_review"


class ResourceType(str, Enum):
    RESCUE_TEAM = "rescue_team"
    RESCUE_BOAT = "rescue_boat"
    AMBULANCE = "ambulance"
    EMERGENCY_VEHICLE = "emergency_vehicle"
    MEDICAL_TEAM = "medical_team"
    TEMPORARY_SHELTER = "temporary_shelter"
    FOOD_WATER = "food_water"
    SPECIALIZED_EQUIPMENT = "specialized_equipment"


class ResourceStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    DEPLOYED = "deployed"
    RETURNING = "returning"
    UNAVAILABLE = "unavailable"
    MAINTENANCE = "maintenance"


class ActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------
# Additions required by the full specification
# --------------------------------------------------------------------------


class SourceType(str, Enum):
    """Originating channel for an incident report (Requirement 2)."""

    EMERGENCY_CALL = "emergency_call"
    FIELD_TEAM = "field_team"
    WEATHER_SERVICE = "weather_service"
    IOT_SENSOR = "iot_sensor"
    GOVERNMENT_ALERT = "government_alert"
    GIS_SYSTEM = "gis_system"
    SHELTER = "shelter"
    HOSPITAL = "hospital"
    RESCUE_TEAM = "rescue_team"
    TRANSPORT_AUTHORITY = "transport_authority"
    CITIZEN_REPORT = "citizen_report"
    MANUAL = "manual"


class RouteStatus(str, Enum):
    """Accessibility state of a route/road.

    ``UNKNOWN`` is deliberately distinct from ``OPEN``: absence of a closure
    report is not evidence that a road is passable.
    """

    OPEN = "open"
    PARTIALLY_BLOCKED = "partially_blocked"
    CLOSED = "closed"
    IMPASSABLE = "impassable"
    UNKNOWN = "unknown"


class FactStatus(str, Enum):
    """Provenance of a route condition.

    Requirement: a *known* closure must never be presented as equivalent to an
    *AI-inferred* accessibility problem.

    ``unknown`` and ``unverified`` are deliberately different claims:

    * ``unknown``   - no report exists at all.
    * ``unverified``- a report exists but did not come from an authority, or
      has not been confirmed yet.

    Conflating them would imply evidence exists where there is none.
    """

    KNOWN = "known"          # reported by an authoritative source
    INFERRED = "inferred"    # derived/inferred by an agent - needs verification
    UNVERIFIED = "unverified"  # a report exists but is not authoritative
    UNKNOWN = "unknown"      # no condition report exists


class AlertType(str, Enum):
    CRITICAL_INCIDENT = "critical_incident"
    SEVERITY_ESCALATED = "severity_escalated"
    ROUTE_BLOCKED = "route_blocked"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    SHELTER_CAPACITY = "shelter_capacity"
    WEATHER_WORSENING = "weather_worsening"
    RESOURCE_SHORTAGE = "resource_shortage"
    ACTION_OVERDUE = "action_overdue"
    DUPLICATE_DETECTED = "duplicate_detected"
    APPROVAL_REQUIRED = "approval_required"
    REPLAN_TRIGGERED = "replan_triggered"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    REASSESSMENT_REQUESTED = "reassessment_requested"


class AllocationStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DeploymentStatus(str, Enum):
    RECOMMENDED = "recommended"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    RETURNING = "returning"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class AccessMode(str, Enum):
    ROAD = "road"
    WATER = "water"
    AIR = "air"
    UNKNOWN = "unknown"


class ShelterStatus(str, Enum):
    OPERATIONAL = "operational"
    FULL = "full"
    CLOSED = "closed"
    EVACUATING = "evacuating"
    UNKNOWN = "unknown"