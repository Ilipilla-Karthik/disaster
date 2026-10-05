"""Shelter capacity management (Requirement 7).

Capacity arithmetic is deterministic and in one place: occupancy changes are
deltas, never absolute overwrites, and over-capacity moves are refused rather
than silently clamped.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, require_commander
from app.models.enums import ShelterStatus
from app.schemas.resource import OccupancyChange, ShelterUpdate
from app.services.alert_service import AlertService
from app.services.shelter_service import CapacityError, ShelterService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shelters", tags=["shelters"])


@router.get("")
async def list_shelters(
    session: AsyncSession = Depends(get_session),
    operational_only: bool = Query(default=False),
) -> dict[str, Any]:
    service = ShelterService(session)
    shelters = await service.list_shelters(operational_only=operational_only)
    return {
        "total": len(shelters),
        "summary": await service.summary(),
        "items": [ShelterService.to_dict(s) for s in shelters],
    }


@router.get("/near-capacity")
async def near_capacity(
    session: AsyncSession = Depends(get_session),
    threshold_pct: float | None = Query(default=None, ge=1, le=100),
) -> dict[str, Any]:
    shelters = await ShelterService(session).near_capacity(threshold_pct)
    return {
        "threshold_pct": threshold_pct,
        "count": len(shelters),
        "items": [ShelterService.to_dict(s) for s in shelters],
    }


@router.get("/{shelter_id}")
async def get_shelter(
    shelter_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    shelter = await ShelterService(session).get(shelter_id)
    if shelter is None:
        raise HTTPException(status_code=404, detail=f"Shelter '{shelter_id}' not found")
    return ShelterService.to_dict(shelter)


@router.patch("/{shelter_id}")
async def update_shelter(
    shelter_id: str,
    payload: ShelterUpdate,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    shelter = await ShelterService(session).update(
        shelter_id, payload.model_dump(exclude_unset=True, mode="json")
    )
    if shelter is None:
        raise HTTPException(status_code=404, detail=f"Shelter '{shelter_id}' not found")
    await session.commit()
    await session.refresh(shelter)
    logger.info("Shelter %s updated by %s", shelter_id, actor["user"])
    return ShelterService.to_dict(shelter)


@router.post("/{shelter_id}/occupancy")
async def change_occupancy(
    shelter_id: str,
    payload: OccupancyChange,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Record people arriving at or leaving a shelter."""
    service = ShelterService(session)
    try:
        shelter = await service.change_occupancy(
            shelter_id, payload.change, reason=payload.reason or actor["user"]
        )
    except CapacityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await AlertService(session).evaluate_shelters([shelter], 85.0)
    await session.commit()
    await session.refresh(shelter)
    return ShelterService.to_dict(shelter)