"""Resource registry and lifecycle (Requirement 6)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_actor, get_session, require_commander
from app.models.enums import ResourceStatus
from app.schemas.resource import (
    ResourceCreate,
    ResourceStatusTransition,
    ResourceUpdate,
)
from app.services.resource_service import InvalidTransition, ResourceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resources"])


def _payload(resource: Any) -> dict[str, Any]:
    return {
        "resource_id": resource.resource_id,
        "resource_type": resource.resource_type.value,
        "location": resource.location,
        "latitude": resource.latitude,
        "longitude": resource.longitude,
        "capability": resource.capability,
        "capability_tags": resource.capability_tags or [],
        "access_modes": resource.access_modes or [],
        "capacity": resource.capacity,
        "status": resource.status.value,
        "deployment_status": resource.deployment_status.value,
        "current_assignment": resource.current_assignment,
        "assigned_at": resource.assigned_at.isoformat() if resource.assigned_at else None,
        "expected_availability": (
            resource.expected_availability.isoformat()
            if resource.expected_availability
            else None
        ),
    }


@router.get("")
async def list_resources(
    session: AsyncSession = Depends(get_session),
    status_filter: ResourceStatus | None = Query(default=None, alias="status"),
    resource_type: str | None = Query(default=None),
    available_only: bool = Query(default=False),
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    service = ResourceService(session)
    resources = await service.list_resources(
        resource_type=resource_type,
        status=status_filter.value if status_filter else None,
    )
    if available_only:
        resources = [r for r in resources if r.status == ResourceStatus.AVAILABLE.value]
    return {
        "total": len(resources),
        "counts": await service.counts_by_status(),
        "items": [_payload(r) for r in resources],
    }


@router.get("/summary")
async def resource_summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    return await ResourceService(session).summary()


@router.get("/{resource_id}")
async def get_resource(
    resource_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    resource = await ResourceService(session).get(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Resource '{resource_id}' not found")
    return _payload(resource)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_resource(
    payload: ResourceCreate,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    resource = await ResourceService(session).create(data)
    await session.commit()
    await session.refresh(resource)
    logger.info("Resource %s created by %s", resource.resource_id, actor["user"])
    return _payload(resource)


@router.patch("/{resource_id}")
async def update_resource(
    resource_id: str,
    payload: ResourceUpdate,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    resource = await ResourceService(session).update(
        resource_id, payload.model_dump(exclude_unset=True, mode="json")
    )
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Resource '{resource_id}' not found")
    await session.commit()
    await session.refresh(resource)
    return _payload(resource)


@router.post("/{resource_id}/transition")
async def transition_resource(
    resource_id: str,
    payload: ResourceStatusTransition,
    session: AsyncSession = Depends(get_session),
    actor: dict[str, str] = Depends(require_commander),
) -> dict[str, Any]:
    """Explicit lifecycle transition.

    The state machine rejects illegal moves (e.g. ``available -> deployed``),
    and reserving or deploying requires the incident the unit serves.
    """
    service = ResourceService(session)
    incident_id = payload.model_dump().get("incident_id")
    try:
        resource = await service.transition(
            resource_id,
            payload.status,
            incident_id=incident_id,
            expected_availability=payload.expected_availability,
            reason=payload.reason or f"by {actor['user']}",
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(resource)
    logger.info(
        "Resource %s -> %s by %s", resource_id, resource.status.value, actor["user"]
    )
    return _payload(resource)