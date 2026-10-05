"""Shelter capacity management (Requirement 7).

All capacity arithmetic is deterministic and integer-based:

    available_capacity = max(0, capacity - current_occupancy)
    utilization_pct    = round(current_occupancy / capacity * 100, 2)

Occupancy can never be driven below zero or above capacity by this service;
over-capacity attempts are rejected with a clear error rather than silently
clamped, because an over-capacity shelter is an operational emergency in
itself and must be escalated rather than hidden.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.models.enums import ShelterStatus
from app.models.models import Shelter

logger = logging.getLogger(__name__)

__all__ = ["ShelterService", "CapacityError"]


class CapacityError(ValueError):
    """Raised when an occupancy change would exceed declared capacity."""


class ShelterService:
    def __init__(self, session) -> None:
        self.session = session

    # -- queries ------------------------------------------------------------

    async def list_shelters(self, operational_only: bool = False) -> list[Shelter]:
        stmt = select(Shelter).order_by(Shelter.shelter_id)
        if operational_only:
            stmt = stmt.where(Shelter.operational_status == ShelterStatus.OPERATIONAL.value)
        return list((await self.session.scalars(stmt)).all())

    async def get(self, shelter_id: str) -> Shelter | None:
        return await self.session.scalar(
            select(Shelter).where(Shelter.shelter_id == shelter_id)
        )

    async def near_capacity(self, threshold_pct: float | None = None) -> list[Shelter]:
        threshold = (
            threshold_pct if threshold_pct is not None else settings.SHELTER_ALERT_THRESHOLD_PCT
        )
        out = []
        for shelter in await self.list_shelters():
            if shelter.capacity and shelter.utilization_pct >= threshold:
                out.append(shelter)
        return out

    async def find_suitable(self, people: int, require_medical: bool = False) -> Shelter | None:
        """Smallest shelter that can accommodate ``people`` (deterministic)."""
        candidates = [
            s
            for s in await self.list_shelters(operational_only=True)
            if s.available_capacity >= people
            and (s.medical_support or not require_medical)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda s: (s.available_capacity, s.shelter_id))[0]

    async def summary(self) -> dict[str, Any]:
        shelters = await self.list_shelters()
        capacity = sum(s.capacity for s in shelters)
        occupancy = sum(s.current_occupancy for s in shelters)
        return {
            "total": len(shelters),
            "capacity": capacity,
            "occupancy": occupancy,
            "available_capacity": max(0, capacity - occupancy),
            "utilization_pct": round(occupancy / capacity * 100, 2) if capacity else 0.0,
            "near_capacity": [
                {
                    "shelter_id": s.shelter_id,
                    "name": s.name,
                    "utilization_pct": s.utilization_pct,
                    "available_capacity": s.available_capacity,
                    "threshold_pct": settings.SHELTER_ALERT_THRESHOLD_PCT,
                }
                for s in await self.near_capacity()
            ],
            "shelters": [self.to_dict(s) for s in shelters],
        }

    @staticmethod
    def to_dict(shelter: Shelter) -> dict[str, Any]:
        return {
            "shelter_id": shelter.shelter_id,
            "name": shelter.name,
            "location": shelter.location,
            "latitude": shelter.latitude,
            "longitude": shelter.longitude,
            "capacity": shelter.capacity,
            "current_occupancy": shelter.current_occupancy,
            "available_capacity": shelter.available_capacity,
            "utilization_pct": shelter.utilization_pct,
            "operational_status": (
                shelter.operational_status.value
                if hasattr(shelter.operational_status, "value")
                else shelter.operational_status
            ),
            "is_accessible": shelter.is_accessible,
            "medical_support": shelter.medical_support,
            "contact_info": shelter.contact_info,
        }

    # -- mutations ----------------------------------------------------------

    async def change_occupancy(
        self, shelter_id: str, change: int, reason: str | None = None
    ) -> Shelter:
        shelter = await self.get(shelter_id)
        if shelter is None:
            raise CapacityError(f"Shelter '{shelter_id}' not found")

        new_occupancy = shelter.current_occupancy + change
        if new_occupancy < 0:
            raise CapacityError(
                f"Occupancy change of {change} would make {shelter_id} negative "
                f"(current {shelter.current_occupancy}). Discharge count exceeds "
                "recorded occupancy - verify shelter returns."
            )
        if new_occupancy > shelter.capacity:
            raise CapacityError(
                f"Occupancy change of {change} would exceed {shelter_id}'s declared "
                f"capacity of {shelter.capacity} (would reach {new_occupancy}). "
                "Escalate for additional shelter provision - the system will not "
                "silently overfill a shelter."
            )

        shelter.current_occupancy = new_occupancy
        if shelter.current_occupancy == shelter.capacity and shelter.capacity:
            shelter.operational_status = ShelterStatus.FULL.value

        logger.info(
            "Shelter %s occupancy %s -> %s (%s)",
            shelter_id,
            new_occupancy - change,
            new_occupancy,
            reason or "no reason given",
        )
        await self.session.flush()
        return shelter

    async def create(self, data: dict[str, Any]) -> Shelter:
        payload = dict(data)
        if not payload.get("shelter_id"):
            count = int(
                await self.session.scalar(select(func.count()).select_from(Shelter)) or 0
            )
            payload["shelter_id"] = f"SH-{count + 1:02d}"
        shelter = Shelter(**payload)
        self.session.add(shelter)
        await self.session.flush()
        return shelter

    async def update(self, shelter_id: str, data: dict[str, Any]) -> Shelter | None:
        shelter = await self.get(shelter_id)
        if shelter is None:
            return None
        for field, value in data.items():
            if value is not None and hasattr(shelter, field):
                setattr(shelter, field, value.value if hasattr(value, "value") else value)
        await self.session.flush()
        return shelter