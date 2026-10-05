"""Resource registry and state machine (Requirement 6).

Lifecycle
---------
    AVAILABLE -> RESERVED -> DEPLOYED -> RETURNING -> AVAILABLE
    (any) -> UNAVAILABLE / MAINTENANCE, and back

Invariants
----------
* A resource in ``RESERVED``/``DEPLOYED``/``RETURNING`` must carry a
  ``current_assignment``. Reservations are what prevent double allocation.
* Only a resource in ``AVAILABLE`` may be reserved or deployed.
* A release clears the assignment and returns the unit to ``AVAILABLE``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.models.enums import DeploymentStatus, ResourceStatus
from app.models.models import Resource

logger = logging.getLogger(__name__)

__all__ = ["ResourceService", "InvalidTransition", "VALID_TRANSITIONS"]


class InvalidTransition(ValueError):
    """Raised when a resource state change would break the lifecycle."""


VALID_TRANSITIONS: dict[ResourceStatus, set[ResourceStatus]] = {
    ResourceStatus.AVAILABLE: {
        ResourceStatus.RESERVED,
        ResourceStatus.DEPLOYED,
        ResourceStatus.UNAVAILABLE,
        ResourceStatus.MAINTENANCE,
    },
    ResourceStatus.RESERVED: {
        ResourceStatus.DEPLOYED,
        ResourceStatus.AVAILABLE,       # release / cancellation
        ResourceStatus.UNAVAILABLE,
    },
    ResourceStatus.DEPLOYED: {
        ResourceStatus.RETURNING,
        ResourceStatus.UNAVAILABLE,
    },
    ResourceStatus.RETURNING: {
        ResourceStatus.AVAILABLE,
        ResourceStatus.MAINTENANCE,
        ResourceStatus.UNAVAILABLE,
    },
    ResourceStatus.UNAVAILABLE: {
        ResourceStatus.AVAILABLE,
        ResourceStatus.MAINTENANCE,
    },
    ResourceStatus.MAINTENANCE: {
        ResourceStatus.AVAILABLE,
        ResourceStatus.UNAVAILABLE,
    },
}

_STATUS_TO_DEPLOYMENT: dict[ResourceStatus, DeploymentStatus] = {
    ResourceStatus.AVAILABLE: DeploymentStatus.RECOMMENDED,
    ResourceStatus.RESERVED: DeploymentStatus.AWAITING_APPROVAL,
    ResourceStatus.DEPLOYED: DeploymentStatus.DEPLOYED,
    ResourceStatus.RETURNING: DeploymentStatus.RETURNING,
    ResourceStatus.UNAVAILABLE: DeploymentStatus.CANCELLED,
    ResourceStatus.MAINTENANCE: DeploymentStatus.RECOMMENDED,
}


class ResourceService:
    def __init__(self, session) -> None:
        self.session = session

    # -- queries ------------------------------------------------------------

    async def list_resources(
        self,
        resource_type: str | None = None,
        status: str | None = None,
        unassigned_only: bool = False,
    ) -> list[Resource]:
        stmt = select(Resource).order_by(Resource.resource_id)
        if resource_type:
            stmt = stmt.where(Resource.resource_type == resource_type)
        if status:
            stmt = stmt.where(Resource.status == status)
        if unassigned_only:
            stmt = stmt.where(
                Resource.current_assignment.is_(None),
                Resource.status == ResourceStatus.AVAILABLE.value,
            )
        return list((await self.session.scalars(stmt)).all())

    async def get(self, resource_id: str) -> Resource | None:
        return await self.session.scalar(
            select(Resource).where(Resource.resource_id == resource_id)
        )

    async def counts_by_status(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(Resource.status, func.count()).group_by(Resource.status)
        )
        out = {status.value if hasattr(status, "value") else status: 0
               for status in ResourceStatus}
        for status, count in rows.all():
            out[status.value if hasattr(status, "value") else status] = int(count)
        out["total"] = sum(
            v for k, v in out.items() if k != "total"
        )
        return out

    async def assigned_ids(self) -> set[str]:
        rows = await self.session.scalars(
            select(Resource.resource_id).where(
                Resource.current_assignment.is_not(None)
            )
        )
        return set(rows.all())

    async def assigned_to(self, incident_id: str) -> list[Resource]:
        return list(
            (
                await self.session.scalars(
                    select(Resource).where(Resource.current_assignment == incident_id)
                )
            ).all()
        )

    # -- mutations ----------------------------------------------------------

    async def transition(
        self,
        resource_id: str,
        new_status: ResourceStatus,
        *,
        incident_id: str | None = None,
        expected_availability: datetime | None = None,
        reason: str | None = None,
        allow_force: bool = False,
    ) -> Resource:
        """Move a resource through the lifecycle, enforcing the invariants."""
        resource = await self.get(resource_id)
        if resource is None:
            raise InvalidTransition(f"Resource '{resource_id}' not found")

        current = ResourceStatus(resource.status)
        target = ResourceStatus(new_status)

        if current == target:
            return resource

        if not allow_force and target not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(
                f"Illegal transition for {resource_id}: {current.value} -> {target.value}. "
                f"Allowed: {sorted(s.value for s in VALID_TRANSITIONS.get(current, set()))}"
            )

        # Reservations/deployments must name the incident they serve.
        if target in {ResourceStatus.RESERVED, ResourceStatus.DEPLOYED}:
            if not incident_id:
                raise InvalidTransition(
                    f"{target.value} requires an incident_id to reserve the unit."
                )
            clash = await self.session.scalar(
                select(Resource).where(
                    Resource.current_assignment == incident_id,
                    Resource.resource_id != resource_id,
                    Resource.status.in_([
                        ResourceStatus.RESERVED.value, ResourceStatus.DEPLOYED.value
                    ]),
                )
            )
            # Not an error: multiple units may serve one incident. Kept as an
            # explicit no-op so intent is visible.
            if clash is not None:
                logger.debug(
                    "%s already has %s assigned; %s is an additional unit.",
                    incident_id, clash.resource_id, resource_id,
                )
            resource.current_assignment = incident_id
            resource.assigned_at = datetime.now(timezone.utc)
        elif target == ResourceStatus.AVAILABLE:
            # Releasing a unit must clear its assignment.
            resource.current_assignment = None
            resource.assigned_at = None
        elif target == ResourceStatus.UNAVAILABLE:
            # An unavailable unit cannot keep an operational assignment.
            resource.current_assignment = None
            resource.assigned_at = None

        resource.status = target.value
        resource.deployment_status = _STATUS_TO_DEPLOYMENT[target].value
        if expected_availability is not None:
            resource.expected_availability = expected_availability
        elif target == ResourceStatus.AVAILABLE:
            resource.expected_availability = None

        logger.info(
            "Resource %s: %s -> %s (%s)",
            resource_id, current.value, target.value, reason or "no reason given",
        )
        await self.session.flush()
        return resource

    async def create(self, data: dict[str, Any]) -> Resource:
        payload = dict(data)
        if not payload.get("resource_id"):
            payload["resource_id"] = await self._next_resource_id()
        payload.setdefault("status", ResourceStatus.AVAILABLE.value)
        payload.setdefault("deployment_status", DeploymentStatus.RECOMMENDED.value)
        resource = Resource(**payload)
        self.session.add(resource)
        await self.session.flush()
        return resource

    async def _next_resource_id(self) -> str:
        prefix = "RES"
        count = int(
            await self.session.scalar(select(func.count()).select_from(Resource)) or 0
        )
        while True:
            candidate = f"{prefix}-{count + 1:03d}"
            clash = await self.session.scalar(
                select(Resource.resource_id).where(Resource.resource_id == candidate)
            )
            if clash is None:
                return candidate
            count += 1

    async def update(self, resource_id: str, data: dict[str, Any]) -> Resource | None:
        resource = await self.get(resource_id)
        if resource is None:
            return None
        for field, value in data.items():
            if value is not None and hasattr(resource, field):
                setattr(resource, field, value.value if hasattr(value, "value") else value)
        await self.session.flush()
        return resource

    async def summary(self) -> dict[str, Any]:
        counts = await self.counts_by_status()
        by_type_rows = await self.session.execute(
            select(Resource.resource_type, func.count()).group_by(Resource.resource_type)
        )
        by_type = {
            (t.value if hasattr(t, "value") else str(t)): int(c)
            for t, c in by_type_rows.all()
        }
        return {
            "total": counts.get("total", 0),
            "available": counts.get(ResourceStatus.AVAILABLE.value, 0),
            "reserved": counts.get(ResourceStatus.RESERVED.value, 0),
            "deployed": counts.get(ResourceStatus.DEPLOYED.value, 0),
            "returning": counts.get(ResourceStatus.RETURNING.value, 0),
            "unavailable": counts.get(ResourceStatus.UNAVAILABLE.value, 0),
            "maintenance": counts.get(ResourceStatus.MAINTENANCE.value, 0),
            "by_type": by_type,
            "state_machine": {
                state.value: sorted(s.value for s in targets)
                for state, targets in VALID_TRANSITIONS.items()
            },
        }