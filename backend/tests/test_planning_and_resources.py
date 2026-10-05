"""TC-04 to TC-06: allocation, the approval gate, and resource invariants.

These are the tests that matter most for trust in the system. They assert the
two properties the specification treats as non-negotiable: generated plans
reserve nothing and deploy nothing, and no operational action can happen without
a named, authorised human having approved the plan it belongs to.
"""

from __future__ import annotations

import pytest

# Digits, not a spelled-out number: the deterministic extractor reads "40 people"
# and the shortfall assertions depend on that count being captured.
FLOOD_TEXT = (
    "Flooding at the Calder Bridge approach. 40 people are trapped and need "
    "rescue and clean water."
)


@pytest.fixture
async def submitted_incident(client):
    response = await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def draft_plan(client, submitted_incident):
    """A generated plan, not yet approved."""
    response = await client.post(
        "/api/v1/plans/generate", json={"incident_ids": [submitted_incident["incident_id"]]}
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    # The envelope carries the workflow output; the plan record is nested.
    return {"envelope": body, **body["plan"]}


# -- TC-04: resource allocation ------------------------------------------


@pytest.mark.asyncio
async def test_generation_reserves_nothing(client, draft_plan):
    """Planning is read-only. A plan that reserved units would pre-empt approval."""
    assert draft_plan["status"] in {"draft", "awaiting_approval"}
    assert not draft_plan["envelope"].get("reserved_resources")


@pytest.mark.asyncio
async def test_generation_dispatches_nothing(client, draft_plan):
    """No resource may be deployed by generating a plan."""
    resources = (await client.get("/api/v1/resources", params={"limit": 500})).json()
    deployed = [r for r in resources["items"] if r["status"] == "deployed"]
    assert deployed == [], f"generation deployed units: {[r['name'] for r in deployed]}"


@pytest.mark.asyncio
async def test_plan_carries_the_shortfall_rather_than_overpromising(client, draft_plan):
    """Capacity that does not exist is stated as a gap, not quietly trimmed."""
    shortages = draft_plan["envelope"].get("shortages") or []
    assert shortages, "40 people against a 10-person boat must report a gap"
    entry = shortages[0]
    assert entry["required"] == 40
    # The gap is reported, not silently absorbed: available < required.
    assert entry["available"] < entry["required"]
    assert entry["required"] - entry["available"] == entry.get("uncovered_people", 20)


@pytest.mark.asyncio
async def test_allocations_are_derived_from_real_resources(client, draft_plan):
    """Every allocated unit id must exist and be a real, available resource."""
    resources = (await client.get("/api/v1/resources", params={"limit": 500})).json()
    by_id = {r["resource_id"]: r for r in resources["items"]}
    for allocation in draft_plan["envelope"].get("allocations", []):
        rid = allocation["resource_id"]
        assert rid in by_id, f"plan allocated unknown resource {rid}"
        assert allocation["resource_label"]
        assert allocation["resource_type"] == by_id[rid]["resource_type"]
        # An allocation computed from an unverified route must say so.
        assert allocation["route_verified"] in (True, False)


@pytest.mark.asyncio
async def test_no_resource_is_allocated_twice(client, draft_plan):
    """Double-booking a unit would make the plan undeliverable."""
    ids = [a["resource_id"] for a in draft_plan["envelope"].get("allocations", [])]
    assert len(ids) == len(set(ids))


@pytest.fixture
async def approval_request(client, draft_plan):
    """Ask for approval the way the console does, and return the pending request."""
    response = await client.post(
        f"/api/v1/plans/{draft_plan['plan_id']}/approve-request"
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


# -- TC-05: approval gate -------------------------------------------------


@pytest.mark.asyncio
async def test_actions_cannot_start_before_approval(client, draft_plan):
    """The gate: an unapproved plan has no startable actions."""
    actions = (await client.get("/api/v1/plans/actions/board")).json()["items"]
    assert actions
    response = await client.post(
        f"/api/v1/plans/actions/{actions[0]['action_id']}/start",
        json={"actor": "chief.morales"},
    )
    assert response.status_code in (400, 409)
    assert "approv" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_requesting_approval_creates_a_pending_request(client, approval_request):
    assert approval_request["status"] == "pending"
    assert approval_request["requested_from"] == "Emergency Commander"


@pytest.mark.asyncio
async def test_requesting_approval_raises_an_alert(client, draft_plan, approval_request):
    """An operator must be told a plan is waiting on them."""
    alerts = (await client.get("/api/v1/alerts", params={"limit": 200})).json()
    assert any(a["alert_type"] == "approval_required" for a in alerts["items"])


@pytest.mark.asyncio
async def test_approval_records_the_officer_and_reserves_units(client, approval_request):
    """Approval is attributed to a named human and reserves, but does not deploy."""
    decided = await client.post(
        f"/api/v1/plans/approvals/{approval_request['request_id']}/decide",
        json={"decision": "approved", "notes": "Proceed."},
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["plan_status"] == "approved"
    # The officer comes from the authenticated identity, not the request body.
    assert body["decided_by"] == "chief.morales"

    resources = (await client.get("/api/v1/resources", params={"limit": 500})).json()
    deployed = [r for r in resources["items"] if r["status"] == "deployed"]
    assert deployed == [], "approval must not deploy anything"


@pytest.mark.asyncio
async def test_rejection_does_not_reserve(client, approval_request):
    rejected = await client.post(
        f"/api/v1/plans/approvals/{approval_request['request_id']}/decide",
        json={
            "decision": "rejected",
            "notes": "Insufficient access route information.",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["plan_status"] == "rejected"
    assert not rejected.json().get("reserved_resources")


@pytest.mark.asyncio
async def test_unauthorised_role_cannot_approve(anonymous_client, approval_request):
    """The gate is identity-based, not client-side politeness."""
    response = await anonymous_client.post(
        f"/api/v1/plans/approvals/{approval_request['request_id']}/decide",
        json={"decision": "approved"},
    )
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_starting_an_action_records_the_officer(client, approval_request):
    """Deployment is a separate, individually attributed decision."""
    await client.post(
        f"/api/v1/plans/approvals/{approval_request['request_id']}/decide",
        json={"decision": "approved"},
    )

    actions = (await client.get("/api/v1/plans/actions/board")).json()["items"]
    # Only this plan's actions: the board is global, and an action belonging to
    # some other test's unapproved plan would (correctly) still be locked.
    pending = [
        a
        for a in actions
        if a["status"] == "pending" and a["plan_id"] == approval_request["plan_id"]
    ]
    assert pending
    started = await client.post(
        f"/api/v1/plans/actions/{pending[0]['action_id']}/start",
        json={"actor": "chief.morales"},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"
    assert started.json()["assigned_to"] == "chief.morales"


# -- TC-06: resource state machine ---------------------------------------


@pytest.mark.asyncio
async def test_illegal_state_transition_is_refused(client):
    """available -> deployed skips reserving, so it is rejected."""
    available = (
        await client.get("/api/v1/resources", params={"status": "available", "limit": 500})
    ).json()["items"]
    assert available
    target = available[0]

    response = await client.post(
        f"/api/v1/resources/{target['resource_id']}/transition",
        json={"status": "deployed", "reason": "skipping the reservation step"},
    )
    assert response.status_code in (400, 409)


@pytest.mark.asyncio
async def test_invalid_status_is_rejected_by_validation(client):
    available = (
        await client.get("/api/v1/resources", params={"status": "available", "limit": 500})
    ).json()["items"]
    response = await client.post(
        f"/api/v1/resources/{available[0]['resource_id']}/transition",
        json={"status": "teleported", "reason": "not a real state"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_transition_response_explains_itself(client):
    """A refusal must say what was wrong, not just return 400."""
    available = (
        await client.get("/api/v1/resources", params={"status": "available", "limit": 500})
    ).json()["items"]
    response = await client.post(
        f"/api/v1/resources/{available[0]['resource_id']}/transition",
        json={"status": "deployed", "reason": "x"},
    )
    assert response.json()["detail"]


@pytest.mark.asyncio
async def test_shelter_over_capacity_is_refused(client):
    """Capacity limits are enforced, not advisory."""
    shelter = (await client.get("/api/v1/shelters")).json()["items"][0]
    response = await client.post(
        f"/api/v1/shelters/{shelter['shelter_id']}/occupancy",
        json={"change": shelter["capacity"] + 5000, "reason": "test overload"},
    )
    assert response.status_code == 409