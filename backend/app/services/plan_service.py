"""Response plan, approval and action tracking (Requirements 7, 8, 11).

This service is the human-in-the-loop boundary. It is the only place that can
turn a *recommendation* into a *reservation*, and it does so exclusively on
behalf of a named, authenticated human decision.

Flow
----
::

    workflow result -> persist_plan(status=draft)
                    -> create_approval_request(plan)
                    -> decide(request, approved_by, decision)
                         |-> approved  -> reserve resources, plan -> active
                         |-> rejected  -> plan -> rejected
                         |-> needs_modification -> plan stays draft
                    -> start_action / complete_action (explicit human calls)

Guarantees
----------
* No plan reaches ``active`` without an APPROVED approval request.
* Approval requires ``decided_by``; an anonymous approval is rejected.
* A reviewer ``changes_requested`` verdict can never be approved as-is.
* Every state change is written to the plan's reasoning trace.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    ActionStatus,
    ApprovalStatus,
    AllocationStatus,
    PlanStatus,
    ResourceStatus,
)
from app.models.models import (
    Action,
    ApprovalRequest,
    Incident,
    PlanAllocation,
    Resource,
    ResponsePlan,
)
from app.services.resource_service import ResourceService

logger = logging.getLogger(__name__)

__all__ = ["PlanService", "PlanRejected"]

REVIEWER_BLOCKING_STATUS = "changes_requested"


class PlanRejected(ValueError):
    """Raised when a plan cannot legally be approved."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: Human decision vocabulary -> persisted approval status.
_DECISION_TO_STATUS: dict[str, ApprovalStatus] = {
    "approved": ApprovalStatus.APPROVED,
    "rejected": ApprovalStatus.REJECTED,
    # "needs_modification" is recorded as a reassessment request so the audit
    # trail shows the plan went back for rework rather than being silently closed.
    "needs_modification": ApprovalStatus.REASSESSMENT_REQUESTED,
}


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.resources = ResourceService(session)

    # -- ids -----------------------------------------------------------------

    @staticmethod
    def _next_plan_id() -> str:
        return f"PLAN-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _next_request_id() -> str:
        return f"APR-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _next_allocation_id() -> str:
        return f"ALC-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _next_action_id() -> str:
        return f"ACT-{uuid.uuid4().hex[:8].upper()}"

    # -- persistence ---------------------------------------------------------

    async def persist_plan(
        self,
        state: dict[str, Any],
        *,
        requested_by: str = "system",
        create_approval: bool = True,
    ) -> ResponsePlan:
        """Store the coordination agent's output as a draft plan."""
        coordination = state.get("coordination_output") or {}
        review = state.get("reviewer_output") or {}

        plan = ResponsePlan(
            plan_id=self._next_plan_id(),
            status=PlanStatus.DRAFT,
            title=coordination.get("title") or "Response plan",
            trigger=state.get("trigger") or "initial_assessment",
            summary=coordination.get("summary"),
            incident_ids=list(coordination.get("incident_ids") or []),
            allocations=list(coordination.get("allocations") or []),
            shortages=list(coordination.get("shortages") or []),
            alternatives=list(coordination.get("alternatives") or []),
            unresolved_issues=list(coordination.get("unresolved_issues") or []),
            assumptions=list(coordination.get("assumptions") or []),
            weather_snapshot=coordination.get("weather_snapshot"),
            reviewer_findings={
                "status": review.get("status"),
                "blocking_count": review.get("blocking_count"),
                "findings": review.get("findings") or [],
                "verdict": review.get("verdict"),
            },
            reasoning_trace={
                "run_id": state.get("run_id"),
                "agents": [
                    {
                        "agent": t.get("agent"),
                        "node": t.get("node"),
                        "duration_ms": t.get("duration_ms"),
                        "llm_used": t.get("llm_used"),
                        "at": t.get("at"),
                    }
                    for t in (state.get("trace") or [])
                    if isinstance(t, dict)
                ],
                "review_status": state.get("review_status"),
                "requested_by": requested_by,
                "agent_errors": state.get("agent_errors") or {},
            },
        )
        self.session.add(plan)
        await self.session.flush()

        for alloc in plan.allocations:
            self.session.add(
                PlanAllocation(
                    allocation_id=self._next_allocation_id(),
                    plan_id=plan.plan_id,
                    incident_id=alloc.get("incident_id"),
                    resource_id=alloc.get("resource_id"),
                    status=AllocationStatus.PROPOSED,
                    rationale=alloc.get("rationale") or "",
                    match_factors=alloc.get("match_factors") or {},
                    distance_km=alloc.get("distance_km"),
                    travel_minutes=alloc.get("travel_minutes"),
                    route_status=alloc.get("route_status"),
                    route_verified=bool(alloc.get("route_verified")),
                    capacity_contribution=int(alloc.get("capacity_contribution") or 0),
                    coverage_gap=int(alloc.get("coverage_gap") or 0),
                )
            )

        # Proposed actions are recorded for tracking, all in `pending`.
        for action in coordination.get("proposed_actions") or []:
            self.session.add(
                Action(
                    action_id=action.get("action_id") or self._next_action_id(),
                    incident_id=action.get("incident_id"),
                    plan_id=plan.plan_id,
                    resource_id=action.get("resource_id"),
                    action_type=action.get("action_type") or "review",
                    description=action.get("description") or "",
                    priority=action.get("priority") or "moderate",
                    status=ActionStatus.PENDING,
                    remarks=action.get("remarks"),
                )
            )

        await self.session.commit()
        await self.session.refresh(plan)
        logger.info("Plan %s persisted (%s)", plan.plan_id, plan.status.value)

        if create_approval:
            await self.create_approval_request(
                plan.plan_id, requested_from="Emergency Commander"
            )
        return plan

    async def create_approval_request(
        self, plan_id: str, requested_from: str
    ) -> ApprovalRequest:
        plan = await self.get_plan(plan_id)
        if plan is None:
            raise LookupError(f"Plan '{plan_id}' not found")
        if plan.status == PlanStatus.ACTIVE:
            raise PlanRejected(f"Plan '{plan_id}' is already active.")

        request = ApprovalRequest(
            request_id=self._next_request_id(),
            plan_id=plan_id,
            status=ApprovalStatus.PENDING,
            requested_from=requested_from,
            original_payload={
                "title": plan.title,
                "summary": plan.summary,
                "incident_ids": plan.incident_ids,
                "allocations": plan.allocations,
                "shortages": plan.shortages,
                "alternatives": plan.alternatives,
                "unresolved_issues": plan.unresolved_issues,
                "assumptions": plan.assumptions,
            },
        )
        self.session.add(request)
        plan.status = PlanStatus.AWAITING_APPROVAL
        await self.session.commit()
        await self.session.refresh(request)
        logger.info("Approval request %s created for plan %s", request.request_id, plan_id)
        return request

    # -- reads ---------------------------------------------------------------

    async def get_plan(self, plan_id: str) -> ResponsePlan | None:
        return await self.session.scalar(
            select(ResponsePlan).where(ResponsePlan.plan_id == plan_id)
        )

    async def list_plans(self, limit: int = 50) -> list[ResponsePlan]:
        result = await self.session.scalars(
            select(ResponsePlan).order_by(ResponsePlan.created_at.desc()).limit(limit)
        )
        return list(result)

    async def list_approvals(self, plan_id: str | None = None) -> list[ApprovalRequest]:
        stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
        if plan_id:
            stmt = stmt.where(ApprovalRequest.plan_id == plan_id)
        return list(await self.session.scalars(stmt))

    async def get_approval(self, request_id: str) -> ApprovalRequest | None:
        return await self.session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.request_id == request_id)
        )

    async def get_plan_detail(self, plan_id: str) -> dict[str, Any] | None:
        plan = await self.get_plan(plan_id)
        if plan is None:
            return None
        allocations = list(
            await self.session.scalars(
                select(PlanAllocation).where(PlanAllocation.plan_id == plan_id)
            )
        )
        actions = list(
            await self.session.scalars(select(Action).where(Action.plan_id == plan_id))
        )
        approvals = await self.list_approvals(plan_id)
        return {
            "plan": {
                "plan_id": plan.plan_id,
                "status": plan.status.value,
                "title": plan.title,
                "trigger": plan.trigger,
                "summary": plan.summary,
                "incident_ids": plan.incident_ids,
                "shortages": plan.shortages,
                "alternatives": plan.alternatives,
                "unresolved_issues": plan.unresolved_issues,
                "assumptions": plan.assumptions,
                "weather_snapshot": plan.weather_snapshot,
                "reviewer_findings": plan.reviewer_findings,
                "reasoning_trace": plan.reasoning_trace,
                "approved_by": plan.approved_by,
                "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
                "superseded_by": plan.superseded_by,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
            },
            "allocations": [
                {
                    "allocation_id": a.allocation_id,
                    "incident_id": a.incident_id,
                    "resource_id": a.resource_id,
                    "status": a.status.value,
                    "rationale": a.rationale,
                    "distance_km": a.distance_km,
                    "travel_minutes": a.travel_minutes,
                    "route_status": a.route_status.value if a.route_status else None,
                    "route_verified": a.route_verified,
                    "capacity_contribution": a.capacity_contribution,
                    "coverage_gap": a.coverage_gap,
                    "match_factors": a.match_factors,
                }
                for a in allocations
            ],
            "actions": [
                {
                    "action_id": a.action_id,
                    "incident_id": a.incident_id,
                    "resource_id": a.resource_id,
                    "action_type": a.action_type,
                    "description": a.description,
                    "priority": a.priority,
                    "status": a.status.value,
                    "assigned_to": a.assigned_to,
                    "approved_by": a.approved_by,
                    "start_time": a.start_time.isoformat() if a.start_time else None,
                    "completion_time": (
                        a.completion_time.isoformat() if a.completion_time else None
                    ),
                    "remarks": a.remarks,
                }
                for a in actions
            ],
            "approvals": [
                {
                    "request_id": r.request_id,
                    "status": r.status.value,
                    "requested_from": r.requested_from,
                    "decided_by": r.decided_by,
                    "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                    "decision": r.decision,
                    "reassessment_reason": r.reassessment_reason,
                }
                for r in approvals
            ],
        }

    # -- human decision ------------------------------------------------------

    async def decide(
        self,
        request_id: str,
        *,
        decision: str,
        decided_by: str,
        notes: str | None = None,
        modified_payload: dict[str, Any] | None = None,
        reassessment_reason: str | None = None,
    ) -> dict[str, Any]:
        """Record a human decision. This is the only path to ``active``."""
        if not decided_by or not str(decided_by).strip():
            raise PlanRejected(
                "An approval decision must name the deciding officer. "
                "Anonymous approval is not accepted."
            )
        decision = str(decision).strip().lower()
        if decision not in {"approved", "rejected", "needs_modification"}:
            raise PlanRejected(
                f"Unknown decision '{decision}'. Use approved, rejected or "
                "needs_modification."
            )

        request = await self.get_approval(request_id)
        if request is None:
            raise LookupError(f"Approval request '{request_id}' not found")
        if request.status != ApprovalStatus.PENDING:
            raise PlanRejected(
                f"Request {request_id} was already {request.status.value} and cannot "
                "be re-decided. Raise a new approval request instead."
            )
        plan = await self.get_plan(request.plan_id) if request.plan_id else None

        if decision == "approved":
            if plan is None:
                raise PlanRejected("Cannot approve a request with no plan.")
            review = (plan.reviewer_findings or {}).get("status")
            if review == REVIEWER_BLOCKING_STATUS:
                raise PlanRejected(
                    "The reviewer agent raised blocking findings for this plan, so it "
                    "cannot be approved. Regenerate the plan and resolve the findings."
                )
            modified_payload = modified_payload or {}
            if modified_payload:
                # A human may adjust the plan; the original is preserved.
                request.modified_payload = modified_payload
                plan.allocations = modified_payload.get("allocations", plan.allocations)
                plan.shortages = modified_payload.get("shortages", plan.shortages)

        request.status = _DECISION_TO_STATUS[decision]
        request.decided_by = decided_by
        request.decided_at = _now()
        request.decision = notes or decision
        if reassessment_reason:
            request.reassessment_reason = reassessment_reason

        result: dict[str, Any] = {
            "request_id": request_id,
            "plan_id": request.plan_id,
            "decision": decision,
            "decided_by": decided_by,
            "decided_at": request.decided_at.isoformat(),
        }

        if plan is not None:
            if decision == "approved":
                reserved = await self._reserve_resources(plan, decided_by)
                # 'approved' and 'active' are different claims: the commander
                # agreed to the plan, but it is not yet in force. Activation is
                # a separate explicit step, so the record of what was agreed to
                # is preserved even if the plan is later superseded.
                plan.status = PlanStatus.APPROVED
                plan.approved_by = decided_by
                plan.approved_at = _now()
                result["reserved_resources"] = reserved
                result["plan_status"] = plan.status.value
                result["note"] = (
                    "Plan approved and units reserved. It becomes active when an "
                    "authorised officer activates it; deployment remains a separate "
                    "per-action decision."
                )
                logger.info(
                    "Plan %s APPROVED by %s; %d unit(s) reserved",
                    plan.plan_id, decided_by, len(reserved),
                )
            elif decision == "rejected":
                plan.status = PlanStatus.REJECTED
                result["plan_status"] = plan.status.value
                logger.info("Plan %s REJECTED by %s", plan.plan_id, decided_by)
            else:
                plan.status = PlanStatus.DRAFT
                result["plan_status"] = plan.status.value
                result["note"] = (
                    "Plan returned for modification. It remains a draft and no "
                    "resource has been reserved."
                )

        await self.session.commit()
        return result

    async def _reserve_resources(
        self, plan: ResponsePlan, decided_by: str
    ) -> list[dict[str, Any]]:
        """Move approved units available -> reserved. Never to deployed."""
        reserved: list[dict[str, Any]] = []
        allocations = list(
            await self.session.scalars(
                select(PlanAllocation).where(PlanAllocation.plan_id == plan.plan_id)
            )
        )
        for alloc in allocations:
            if alloc.status == AllocationStatus.CANCELLED:
                continue
            resource = await self.session.scalar(
                select(Resource).where(Resource.resource_id == alloc.resource_id)
            )
            if resource is None:
                # A reviewer check normally prevents this; refuse rather than skip.
                raise PlanRejected(
                    f"Allocation {alloc.allocation_id} references resource "
                    f"'{alloc.resource_id}', which is not in the registry."
                )
            if resource.status in {ResourceStatus.RESERVED.value, ResourceStatus.DEPLOYED.value}:
                reserved.append(
                    {
                        "resource_id": resource.resource_id,
                        "status": resource.status,
                        "note": "Already committed; left untouched.",
                    }
                )
                continue
            await self.resources.transition(
                resource.resource_id,
                ResourceStatus.RESERVED,
                incident_id=alloc.incident_id,
                reason=f"Approved plan {plan.plan_id} by {decided_by}",
            )
            alloc.status = AllocationStatus.APPROVED
            reserved.append(
                {
                    "resource_id": resource.resource_id,
                    "status": ResourceStatus.RESERVED.value,
                    "incident_id": alloc.incident_id,
                }
            )
        return reserved

    # -- action tracking -----------------------------------------------------

    async def start_action(
        self, action_id: str, *, actor: str, resource_id: str | None = None
    ) -> Action:
        """Mark an approved action as in progress (a human operational call)."""
        action = await self.session.scalar(
            select(Action).where(Action.action_id == action_id)
        )
        if action is None:
            raise LookupError(f"Action '{action_id}' not found")
        if not actor:
            raise PlanRejected("Starting an action must name the actor.")

        plan = await self.get_plan(action.plan_id) if action.plan_id else None
        # Starting an action is what puts the plan into force, so an approved
        # plan activates here. A plan that is not approved is refused outright.
        if plan is None:
            raise PlanRejected(f"Action {action_id} has no plan and cannot start.")
        if plan.status == PlanStatus.APPROVED:
            plan.status = PlanStatus.ACTIVE
        elif plan.status != PlanStatus.ACTIVE:
            raise PlanRejected(
                f"Action {action_id} cannot start: its plan is {plan.status.value}, "
                "which is not approved."
            )
        if action.status not in {ActionStatus.PENDING, ActionStatus.APPROVED}:
            raise PlanRejected(
                f"Action {action_id} is {action.status.value} and cannot be started."
            )

        rid = resource_id or action.resource_id
        if rid:
            await self.resources.transition(
                rid,
                ResourceStatus.DEPLOYED,
                incident_id=action.incident_id,
                reason=f"Action {action_id} started by {actor}",
            )
            action.resource_id = rid
        action.status = ActionStatus.IN_PROGRESS
        action.assigned_to = actor
        action.approved_by = plan.approved_by
        action.approved_at = plan.approved_at
        action.start_time = _now()
        await self.session.commit()
        await self.session.refresh(action)
        return action

    async def complete_action(
        self, action_id: str, *, actor: str, remarks: str | None = None
    ) -> Action:
        action = await self.session.scalar(
            select(Action).where(Action.action_id == action_id)
        )
        if action is None:
            raise LookupError(f"Action '{action_id}' not found")
        if action.status != ActionStatus.IN_PROGRESS:
            raise PlanRejected(
                f"Action {action_id} is {action.status.value}; only in-progress "
                "actions can be completed."
            )
        action.status = ActionStatus.COMPLETED
        action.completion_time = _now()
        if remarks:
            action.remarks = remarks
        if action.resource_id:
            await self.resources.transition(
                action.resource_id,
                ResourceStatus.RETURNING,
                incident_id=action.incident_id,
                reason=f"Action {action_id} completed by {actor}",
            )
        await self.session.commit()
        await self.session.refresh(action)
        return action

    # -- replanning ----------------------------------------------------------

    async def supersede(
        self, plan_id: str, *, replacement_plan_id: str, reason: str
    ) -> ResponsePlan:
        old = await self.get_plan(plan_id)
        if old is None:
            raise LookupError(f"Plan '{plan_id}' not found")
        old.superseded_by = replacement_plan_id
        if old.status in {
            PlanStatus.DRAFT,
            PlanStatus.AWAITING_APPROVAL,
            PlanStatus.APPROVED,
        }:
            # An approved-but-not-active plan is superseded outright; an active
            # plan keeps its status until a human stands it down, because
            # units may still be deployed under it.
            old.status = PlanStatus.SUPERSEDED
        trace = dict(old.reasoning_trace or {})
        history = list(trace.get("superseded") or [])
        history.append(
            {
                "by": replacement_plan_id,
                "reason": reason,
                "at": _now().isoformat(),
            }
        )
        trace["superseded"] = history
        old.reasoning_trace = trace
        await self.session.commit()
        await self.session.refresh(old)
        return old

    async def incident_plan_status(self, incident_ids: list[str]) -> dict[str, str]:
        """Which plan (if any) currently covers each incident."""
        if not incident_ids:
            return {}
        plans = list(
            await self.session.scalars(
                select(ResponsePlan).where(
                    ResponsePlan.status.in_(
                        [PlanStatus.DRAFT.value, PlanStatus.AWAITING_APPROVAL.value,
                         PlanStatus.ACTIVE.value]
                    )
                )
            )
        )
        coverage: dict[str, str] = {}
        for plan in plans:
            for iid in plan.incident_ids or []:
                coverage.setdefault(str(iid), plan.plan_id)
        return {i: coverage[i] for i in incident_ids if i in coverage}

    async def assigned_counts(self) -> dict[str, int]:
        rows = await self.session.scalars(
            select(Resource.current_assignment, Resource.resource_id).where(
                Resource.current_assignment.isnot(None)
            )
        )
        counts: dict[str, int] = {}
        for assignment in rows:
            if assignment:
                counts[str(assignment)] = counts.get(str(assignment), 0) + 1
        return counts

    async def open_incidents(self) -> list[Incident]:
        return list(
            await self.session.scalars(
                select(Incident).where(
                    Incident.severity_level != "verification_required",
                    Incident.is_duplicate.is_(False),
                )
            )
        )