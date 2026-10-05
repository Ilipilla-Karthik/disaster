"""Tunable constants for the deterministic decision engines.

Keeping every weight in one documented place satisfies the requirement that
"the LLM should not arbitrarily generate severity scores" - all numeric
scoring is reproducible from these tables.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DOCS_DIR = PROJECT_ROOT / "docs"
TEST_SCENARIOS_DIR = PROJECT_ROOT / "tests_scenarios"

# --------------------------------------------------------------------------
# Priority scoring model  (Requirement 4)
#
# Total = sum of weighted factors, clamped to [0, 100]. Each factor is a pure
# function of verified input data; nothing is randomised or LLM-generated.
# --------------------------------------------------------------------------
PRIORITY_WEIGHTS: dict[str, float] = {
    # Threat to life: people reported affected (log-scaled, capped).
    "people_affected": 30.0,
    # Type-implied lethality / time-criticality of the hazard.
    "incident_type_risk": 15.0,
    # Access constraint (closed road isolates people).
    "access_restriction": 15.0,
    # Deteriorating weather raises escalation potential.
    "weather_deterioration": 12.0,
    # Infrastructure / lifeline failure.
    "infrastructure_impact": 8.0,
    # Current response gap: higher when nothing is assigned yet.
    "response_gap": 10.0,
    # Reported uncertainty - drives "Verification Required" handling.
    "uncertainty": 5.0,
    # Explicit human escalation request.
    "escalation_signal": 5.0,
}

# Maximum contribution of each factor before clamping.
PRIORITY_FACTOR_CAPS: dict[str, float] = {
    "people_affected": 30.0,
    "incident_type_risk": 15.0,
    "access_restriction": 15.0,
    "weather_deterioration": 12.0,
    "infrastructure_impact": 8.0,
    "response_gap": 10.0,
    "uncertainty": 5.0,
    "escalation_signal": 5.0,
}

# Severity band boundaries on the 0-100 priority score.
SEVERITY_BANDS: list[tuple[float, str]] = [
    (80.0, "critical"),
    (60.0, "high"),
    (40.0, "moderate"),
    (20.0, "low"),
    (0.0, "verification_required"),
]

# Risk contribution by incident type (0-1), from published hazard characteristics.
INCIDENT_TYPE_RISK: dict[str, float] = {
    "flood": 0.85,
    "cyclone": 0.95,
    "earthquake": 0.90,
    "wildfire": 0.80,
    "landslide": 0.70,
    "industrial_accident": 0.90,
    "other": 0.50,
}

# --------------------------------------------------------------------------
# Priority model support tables
# --------------------------------------------------------------------------

# How a route condition affects isolation of an incident (0-1).
ROUTE_STATUS_IMPACT: dict[str, float] = {
    "open": 0.0,
    "partially_blocked": 0.5,
    "unknown": 0.4,      # unknown is treated as a precaution, never as "fine"
    "closed": 1.0,
    "impassable": 1.0,
}

# Additive weather deterioration weights (sum is normalised against WEATHER_MAX).
WEATHER_DETERIORATION_WEIGHTS: dict[str, float] = {
    "worsening": 6.0,
    "severe_warning": 8.0,
    "storm": 4.0,
    "high_wind": 3.0,
}
WEATHER_MAX = 25.0

# Capability tags required to serve each incident type.
REQUIRED_CAPABILITIES: dict[str, list[str]] = {
    "flood": ["water_rescue"],
    "cyclone": ["storm_response"],
    "earthquake": ["structural_response"],
    "wildfire": ["fire_suppression"],
    "landslide": ["heavy_equipment"],
    "industrial_accident": ["hazmat"],
    "other": [],
}

# Access modes that are considered feasible when reaching an incident.
ACCESS_MODE_BY_INCIDENT_TYPE: dict[str, list[str]] = {
    "flood": ["water", "road"],
    "cyclone": ["road"],
    "earthquake": ["road"],
    "wildfire": ["road"],
    "landslide": ["road"],
    "industrial_accident": ["road"],
    "other": ["road"],
}

# Resource types considered suitable per incident type (deterministic filter).
SUITABLE_RESOURCE_TYPES: dict[str, list[str]] = {
    "flood": ["rescue_boat", "rescue_team", "ambulance", "temporary_shelter", "food_water"],
    "cyclone": ["rescue_team", "emergency_vehicle", "medical_team", "temporary_shelter", "food_water"],
    "earthquake": ["rescue_team", "specialized_equipment", "medical_team", "ambulance", "temporary_shelter"],
    "wildfire": ["specialized_equipment", "rescue_team", "ambulance", "temporary_shelter", "food_water"],
    "landslide": ["specialized_equipment", "rescue_team", "ambulance", "emergency_vehicle"],
    "industrial_accident": ["medical_team", "specialized_equipment", "ambulance", "rescue_team"],
    "other": ["rescue_team", "ambulance", "emergency_vehicle", "temporary_shelter"],
}

# Capability tags per resource type, used for explainable matching.
RESOURCE_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "rescue_team": ["water_rescue", "evacuation", "search_rescue", "first_aid"],
    "rescue_boat": ["water_rescue", "evacuation", "swift_water"],
    "ambulance": ["medical_transport", "first_aid"],
    "emergency_vehicle": ["logistics", "evacuation", "command_support"],
    "medical_team": ["medical_triage", "first_aid", "medical_transport"],
    "temporary_shelter": ["shelter", "shelter_capacity"],
    "food_water": ["sustenance"],
    "specialized_equipment": [
        "pumping",
        "heavy_equipment",
        "fire_suppression",
        "hazmat",
        "structural_response",
        "storm_response",
        "search_rescue",
    ],
}

# Access modes each resource type can physically use.
RESOURCE_TYPE_ACCESS_MODES: dict[str, list[str]] = {
    "rescue_team": ["road"],
    "rescue_boat": ["water"],
    "ambulance": ["road"],
    "emergency_vehicle": ["road"],
    "medical_team": ["road"],
    "temporary_shelter": ["road"],
    "food_water": ["road"],
    "specialized_equipment": ["road", "water"],
}

# Maximum useful deployable radius (km) before a resource is considered
# unsuitable on grounds of being too far away.
MAX_DEPLOYMENT_RADIUS_KM: dict[str, float] = {
    "rescue_boat": 40.0,
    "rescue_team": 60.0,
    "ambulance": 50.0,
    "emergency_vehicle": 80.0,
    "medical_team": 60.0,
    "specialized_equipment": 70.0,
    "temporary_shelter": 100.0,
    "food_water": 80.0,
}

# --------------------------------------------------------------------------
# Duplicate detection (Requirement 3)
# --------------------------------------------------------------------------
DUPLICATE_WEIGHTS: dict[str, float] = {
    "type": 0.25,
    "location": 0.25,
    "proximity": 0.20,
    "temporal": 0.15,
    "text": 0.15,
}

# --------------------------------------------------------------------------
# Allocation scoring
# --------------------------------------------------------------------------
ALLOCATION_WEIGHTS: dict[str, float] = {
    "capability": 40.0,
    "distance": 25.0,
    "availability": 15.0,
    "capacity": 10.0,
    "access": 10.0,
}

DISCLAIMER = (
    "DECISION SUPPORT ONLY. This prototype does not dispatch emergency "
    "personnel, control vehicles, issue evacuation orders, or provide medical "
    "triage. All recommendations require approval by an authorised emergency "
    "commander before any action is taken."
)

VERIFICATION_NOTE = "Verification Required"