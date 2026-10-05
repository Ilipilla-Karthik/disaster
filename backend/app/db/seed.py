"""Synthetic emergency simulation dataset.

Seeds the assignment scenario: a severe flood affecting Zone A, Zone B and
Zone C of "Example District" during continued heavy rainfall.

This is **synthetic demonstration data only**. It is not real operational
data, must not be used for real emergency planning, and exists so the platform
can be exercised end-to-end without provisioning live feeds.

Roads are intentionally seeded with ``status="unknown"`` /
``fact_status="unknown"`` so the platform starts from "Verification
Required" rather than silently assuming every route is passable. The two go
together: ``unverified`` would imply a report exists that has not been
confirmed, and for these roads no report exists at all.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select

from app.models.enums import (
    AccessMode,
    DeploymentStatus,
    FactStatus,
    ResourceStatus,
    ResourceType,
    RouteStatus,
    ShelterStatus,
)
from app.models.models import (
    Action,
    AgentRunLog,
    Alert,
    ApprovalRequest,
    Incident,
    PlanAllocation,
    Resource,
    ResponsePlan,
    Road,
    Shelter,
    SituationReport,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static reference data
# ---------------------------------------------------------------------------

CENTERS: dict[str, tuple[float, float]] = {
    "Response Center 1": (12.9716, 77.5946),
    "Response Center 2": (12.9800, 77.6000),
    "Zone B Staging": (12.9600, 77.6100),
}

# resource_type -> (id_prefix, count, capacity, capability_tags, access_modes)
RESOURCE_SPECS: list[tuple[str, str, int, int, list[str], list[str]]] = [
    (
        ResourceType.RESCUE_BOAT.value,
        "BOAT",
        5,
        8,
        ["water_rescue", "evacuation", "swift_water"],
        [AccessMode.WATER.value],
    ),
    (
        ResourceType.RESCUE_TEAM.value,
        "TEAM",
        4,
        10,
        ["water_rescue", "evacuation", "search_rescue", "first_aid"],
        [AccessMode.ROAD.value],
    ),
    (
        ResourceType.AMBULANCE.value,
        "AMB",
        6,
        4,
        ["medical_transport", "first_aid"],
        [AccessMode.ROAD.value],
    ),
    (
        ResourceType.EMERGENCY_VEHICLE.value,
        "EV",
        8,
        12,
        ["logistics", "evacuation", "command_support"],
        [AccessMode.ROAD.value],
    ),
    (
        ResourceType.TEMPORARY_SHELTER.value,
        "SHELTER",
        3,
        50,
        ["shelter", "shelter_capacity"],
        [AccessMode.ROAD.value],
    ),
]

SHELTER_ROWS: list[dict[str, Any]] = [
    {
        "shelter_id": "SH-A",
        "name": "Shelter A",
        "location": "Zone B Community Hall",
        "latitude": 12.9620,
        "longitude": 77.6070,
        "capacity": 200,
        "current_occupancy": 160,
        "operational_status": ShelterStatus.OPERATIONAL.value,
        "is_accessible": True,
        "medical_support": False,
        "contact_info": "Zone B Community Hall - shelter warden on duty",
    },
    {
        "shelter_id": "SH-B",
        "name": "Shelter B",
        "location": "Zone C School Grounds",
        "latitude": 12.9500,
        "longitude": 77.6050,
        "capacity": 150,
        "current_occupancy": 60,
        "operational_status": ShelterStatus.OPERATIONAL.value,
        "is_accessible": True,
        "medical_support": True,
        "contact_info": "Zone C School Grounds - medical tent staffed",
    },
    {
        "shelter_id": "SH-C",
        "name": "Shelter C",
        "location": "Zone A Sports Complex",
        "latitude": 12.9760,
        "longitude": 77.5900,
        "capacity": 300,
        "current_occupancy": 110,
        "operational_status": ShelterStatus.OPERATIONAL.value,
        "is_accessible": True,
        "medical_support": False,
        "contact_info": "Zone A Sports Complex - municipal operations desk",
    },
]

ROAD_ROWS: list[dict[str, Any]] = [
    {
        "road_id": "RD-001",
        "name": "Main Road to Zone A",
        "origin_zone": "Response Center 1",
        "destination_zone": "Zone A",
        "latitude": 12.9735,
        "longitude": 77.5920,
        "length_km": 4.2,
        "access_mode": AccessMode.ROAD.value,
    },
    {
        "road_id": "RD-002",
        "name": "Main Road to Zone B",
        "origin_zone": "Response Center 1",
        "destination_zone": "Zone B",
        "latitude": 12.9610,
        "longitude": 77.6080,
        "length_km": 6.1,
        "access_mode": AccessMode.ROAD.value,
    },
    {
        "road_id": "RD-003",
        "name": "Main Road to Zone C",
        "origin_zone": "Response Center 1",
        "destination_zone": "Zone C",
        "latitude": 12.9510,
        "longitude": 77.6040,
        "length_km": 8.7,
        "access_mode": AccessMode.ROAD.value,
    },
    {
        "road_id": "RD-004",
        "name": "Zone B Link Road",
        "origin_zone": "Zone B",
        "destination_zone": "Zone C",
        "latitude": 12.9560,
        "longitude": 77.6065,
        "length_km": 3.4,
        "access_mode": AccessMode.ROAD.value,
    },
    {
        "road_id": "RD-005",
        "name": "Zone A Bypass",
        "origin_zone": "Zone A",
        "destination_zone": "Response Center 1",
        "latitude": 12.9745,
        "longitude": 77.5935,
        "length_km": 4.6,
        "access_mode": AccessMode.ROAD.value,
    },
    {
        "road_id": "RD-006",
        "name": "Zone C Causeway",
        "origin_zone": "Zone C",
        "destination_zone": "Response Center 1",
        "latitude": 12.9525,
        "longitude": 77.6030,
        "length_km": 9.2,
        "access_mode": AccessMode.WATER.value,
    },
]


async def _wipe(session) -> None:
    """Delete all rows in FK-safe order."""
    for model in (
        Alert,
        Action,
        PlanAllocation,
        ApprovalRequest,
        SituationReport,
        ResponsePlan,
        Incident,
        Resource,
        Shelter,
        Road,
        AgentRunLog,
    ):
        await session.execute(delete(model))
    await session.commit()


async def _seed_resources(session) -> int:
    """Create the resource registry: 5 boats, 4 teams, 6 ambulances, 8 vehicles, 3 shelter units."""
    rows: list[Resource] = []
    centre_names = list(CENTERS.keys())
    counter = 0

    for rtype, prefix, count, capacity, tags, modes in RESOURCE_SPECS:
        for index in range(1, count + 1):
            centre = centre_names[counter % len(centre_names)]
            lat, lon = CENTERS[centre]
            rows.append(
                Resource(
                    resource_id=f"{prefix}-{index:03d}",
                    resource_type=rtype,
                    location=centre,
                    latitude=lat,
                    longitude=lon,
                    capability=f"{rtype.replace('_', ' ').title()} - unit {index} ({centre})",
                    capability_tags=tags,
                    capacity=capacity,
                    access_modes=modes,
                    status=ResourceStatus.AVAILABLE.value,
                    deployment_status=DeploymentStatus.RECOMMENDED.value,
                )
            )
            counter += 1

    session.add_all(rows)
    await session.flush()
    return len(rows)


async def _seed_shelters(session) -> int:
    rows = [Shelter(**row) for row in SHELTER_ROWS]
    session.add_all(rows)
    await session.flush()
    return len(rows)


async def _seed_roads(session) -> int:
    """Seed routes as UNKNOWN/unverified - absence of a closure is not evidence of access."""
    rows: list[Road] = []
    for spec in ROAD_ROWS:
        payload = {
            key: value for key, value in spec.items() if key != "access_mode"
        }
        rows.append(
            Road(
                **payload,
                status=RouteStatus.UNKNOWN.value,
                fact_status=FactStatus.UNKNOWN.value,
                access_mode=spec["access_mode"],
            )
        )
    session.add_all(rows)
    await session.flush()
    return len(rows)


async def _current_counts(session) -> dict[str, int]:
    async def count(model) -> int:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    return {
        "resources": await count(Resource),
        "shelters": await count(Shelter),
        "roads": await count(Road),
        "incidents": await count(Incident),
    }


async def seed_all(session, force: bool = False) -> dict[str, Any]:
    """Populate the synthetic demo dataset.

    Idempotent: returns early with ``skipped=True`` when data already exists
    unless ``force`` is set, in which case everything is wiped and rebuilt.

    Incidents, alerts and actions are intentionally left empty - they are
    produced by the agent pipeline and the test scenarios, not by the seed.
    """
    existing = await _current_counts(session)
    if not force and any(existing.values()):
        logger.info("Seed skipped - data already present: %s", existing)
        return {**existing, "skipped": True}

    if force:
        await _wipe(session)

    now = datetime.now(timezone.utc)
    resources = await _seed_resources(session)
    shelters = await _seed_shelters(session)
    roads = await _seed_roads(session)
    await session.commit()

    result = {
        "resources": resources,
        "shelters": shelters,
        "roads": roads,
        "incidents": 0,
        "alerts": 0,
        "actions": 0,
        "seeded_at": now.isoformat(),
        "skipped": False,
    }
    logger.info(
        "Seeded simulation dataset: %s resources, %s shelters, %s roads",
        resources,
        shelters,
        roads,
    )
    return result