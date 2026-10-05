"""TC-01 to TC-03: incident intake, duplicate detection, severity assessment.

The theme running through these tests: the system must never upgrade what it does
not know into what it claims. An unresolved location stays unresolved, a
near-duplicate report is linked rather than merged, and an unverified report
cannot acquire a confident severity band.
"""

from __future__ import annotations

import pytest

from app.services.duplicate_detector import DuplicateDetector
from app.services.normalizer import IncidentNormalizer, extract_from_text
from app.services.priority_calculator import PriorityCalculator

# The database is shared for the whole module, so the duplicate pair needs
# vocabulary no other test uses. Otherwise the detector legitimately matches a
# neighbouring test's incident - which is correct behaviour, but makes this
# assertion test the wrong record.
FLOOD_TEXT = (
    "Flooding reported at the Kestrel Lane depot. About 40 people are stranded "
    "by the Zulu-9 rail bridge."
)
NEAR_DUPLICATE_TEXT = (
    "Water rising at the Kestrel Lane depot again, roughly 40 residents "
    "stranded by the Zulu-9 rail bridge."
)


# -- TC-01: unstructured intake ------------------------------------------


@pytest.mark.asyncio
async def test_raw_text_is_normalised_without_structured_fields(client):
    """A citizen phone call arrives as prose. It still becomes a usable incident."""
    response = await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    assert response.status_code == 201, response.text

    incident = response.json()
    assert incident["incident_type"] == "flood"
    assert incident["people_reported_affected"] == 40
    assert incident["incident_id"].startswith("INC-")


@pytest.mark.asyncio
async def test_unresolved_fields_are_flagged_not_guessed(client):
    """Nothing invented: unresolved fields are named explicitly."""
    incident = (
        await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    ).json()

    assert incident["intake"]["verification_required"] is True
    assert "location" in incident["missing_fields"]
    # Never a number the report did not contain.
    assert incident["latitude"] is None and incident["longitude"] is None
    # An unverified incident must not carry a confident severity.
    assert incident["severity"] == "verification_required"


@pytest.mark.asyncio
async def test_extraction_method_is_reported(client):
    """The operator can see whether a model or keyword rules did the extraction."""
    incident = (
        await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    ).json()
    assert incident["intake"]["extraction_method"] in {
        "llm",
        "deterministic_keywords",
    }


@pytest.mark.asyncio
async def test_missing_fields_are_not_duplicated(client):
    incident = (
        await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    ).json()
    assert len(incident["missing_fields"]) == len(set(incident["missing_fields"]))


@pytest.mark.asyncio
async def test_structured_fields_override_extracted_ones(client):
    """A human who typed the type is believed over the keyword extractor."""
    response = await client.post(
        "/api/v1/incidents",
        json={
            "raw_text": FLOOD_TEXT,
            "incident_type": "industrial_accident",
            "people_reported_affected": 5,
        },
    )
    incident = response.json()
    assert incident["incident_type"] == "industrial_accident"
    assert incident["people_reported_affected"] == 5


@pytest.mark.asyncio
async def test_unknown_incident_type_is_rejected_not_silently_coerced(client):
    """Strict vocabulary: 'structural_fire' is a 422, not a guess at 'wildfire'."""
    response = await client.post(
        "/api/v1/incidents", json={"raw_text": "Structural fire in Zone C."}
    )
    # Accepted as free text, but the type is left unresolved for a human.
    assert response.status_code == 201
    assert response.json()["incident_type"] == "other"
    assert "incident_type" in response.json()["missing_fields"]

    strict = await client.post(
        "/api/v1/incidents", json={"incident_type": "structural_fire"}
    )
    assert strict.status_code == 422


@pytest.mark.asyncio
async def test_empty_report_is_rejected(client):
    """A payload with neither text nor structure is not a report."""
    response = await client.post("/api/v1/incidents", json={})
    assert response.status_code == 422


def test_structural_fire_is_not_labelled_wildfire():
    """The vocabulary has no urban-fire type; it must not borrow wildfire's."""
    record = IncidentNormalizer().normalize(
        {"description": "Structural fire in Zone C. Two buildings affected."}
    )
    assert record["incident_type"].value == "other"
    assert "incident_type" in record["missing_fields"]


def test_extraction_of_people_and_assistance():
    extracted = extract_from_text(
        "Flooding in Zone A. About 25 people need evacuation and clean water.",
        "citizen_app",
    )
    assert extracted["people_reported_affected"] == 25
    assert "assistance_requested" in extracted


# -- TC-02: duplicate detection ------------------------------------------


@pytest.mark.asyncio
async def test_near_duplicate_is_linked_not_merged(client):
    """Two calls about one event produce two records and one link."""
    first = (await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})).json()
    second = (
        await client.post("/api/v1/incidents", json={"raw_text": NEAR_DUPLICATE_TEXT})
    ).json()

    assert second["is_duplicate"] is True
    # Links resolve to the canonical root of the cluster, so a chain of reports
    # about one event all point at the same incident rather than at each other.
    canonical_id = first["duplicate_of"] or first["incident_id"]
    assert second["duplicate_of"] == canonical_id

    # Both records survive: a duplicate link is for a human, not a deletion.
    listing = (await client.get("/api/v1/incidents")).json()
    assert listing["total"] >= 2


@pytest.mark.asyncio
async def test_duplicate_raises_an_alert_for_review(client):
    await client.post("/api/v1/incidents", json={"raw_text": FLOOD_TEXT})
    duplicate = (
        await client.post("/api/v1/incidents", json={"raw_text": NEAR_DUPLICATE_TEXT})
    ).json()

    alerts = (await client.get("/api/v1/alerts", params={"limit": 200})).json()
    duplicates = [
        a for a in alerts["items"] if a["alert_type"] == "duplicate_detected"
    ]
    assert duplicates, "a flagged duplicate must be surfaced to an operator"
    assert any(a["incident_id"] == duplicate["incident_id"] for a in duplicates)


def test_different_hazards_are_not_duplicates():
    """Type disagreement is disqualifying regardless of wording overlap."""
    detector = DuplicateDetector()
    matches = detector.find_duplicates(
        {
            "incident_id": "B",
            "incident_type": "flood",
            "location": "Zone A",
            "description": "Flooding in Zone A, 40 people trapped.",
            "reported_at": "2026-10-05T10:00:00+00:00",
        },
        [
            {
                "incident_id": "A",
                "incident_type": "earthquake",
                "location": "Zone A",
                "description": "Flooding in Zone A, 40 people trapped.",
                "reported_at": "2026-10-05T10:01:00+00:00",
            }
        ],
    )
    assert matches == []


def test_unrelated_reports_are_not_duplicates():
    """Same hazard type and timing is not enough without shared specifics."""
    detector = DuplicateDetector()
    matches = detector.find_duplicates(
        {
            "incident_id": "B",
            "incident_type": "flood",
            "location": "Unknown",
            "description": "Gas leak reported at the pharmacy on Main Street.",
            "reported_at": "2026-10-05T10:00:00+00:00",
        },
        [
            {
                "incident_id": "A",
                "incident_type": "flood",
                "location": "Unknown",
                "description": "Flooding on Riverside Avenue, 40 people trapped.",
                "reported_at": "2026-10-05T10:05:00+00:00",
            }
        ],
    )
    assert matches == []


def test_enum_and_string_types_compare_equal():
    """Normalised records hold enums; ORM rows hold strings. They are the same type."""
    detector = DuplicateDetector()
    from app.models.enums import IncidentType

    matches = detector.find_duplicates(
        {
            "incident_id": "B",
            "incident_type": IncidentType.FLOOD,
            "location": "Zone A",
            "description": "Water rising in Zone A, 40 people stranded.",
            "reported_at": "2026-10-05T10:00:00+00:00",
        },
        [
            {
                "incident_id": "A",
                "incident_type": "flood",
                "location": "Zone A",
                "description": "Rising water in Zone A, 40 people affected.",
                "reported_at": "2026-10-05T10:02:00+00:00",
            }
        ],
    )
    assert matches and matches[0].duplicate_of == "A"


# -- TC-03: severity and priority ----------------------------------------


def test_more_affected_people_scores_higher():
    """The priority score responds to the deterministic factors, nothing else."""
    calculator = PriorityCalculator()
    small = calculator.calculate(
        {"incident_type": "flood", "people_reported_affected": 2, "location": "Zone A"}
    )
    large = calculator.calculate(
        {
            "incident_type": "flood",
            "people_reported_affected": 500,
            "location": "Zone A",
        }
    )
    assert large.score > small.score
    assert 0 <= small.score <= 100


def test_unverified_data_cannot_produce_a_confident_band():
    """A report missing its essentials is 'verification required', not 'low'."""
    calculator = PriorityCalculator()
    result = calculator.calculate(
        {
            "incident_type": "flood",
            "location": "Unknown",
            "verification_status": "pending",
            "missing_fields": ["location", "coordinates"],
        }
    )
    assert result.severity.value == "verification_required"
    assert result.requires_verification is True
    assert result.verification_required_fields
    # The numeric band is preserved so a reviewer can still judge it.
    assert result.unverified_severity.value == "low"


def test_verified_low_incident_keeps_its_band():
    """Once verified, a genuinely low band is reported as low."""
    result = PriorityCalculator().calculate(
        {
            "incident_type": "flood",
            "people_reported_affected": 1,
            "location": "Zone A",
            "verification_status": "verified",
            "missing_fields": [],
        }
    )
    assert result.severity.value == "low"
    assert result.requires_verification is False


def test_priority_score_is_deterministic():
    """Same input, same score. No LLM in the loop."""
    calculator = PriorityCalculator()
    payload = {
        "incident_type": "flood",
        "people_reported_affected": 40,
        "location": "Zone A",
        "infrastructure_issue": "road",
    }
    assert calculator.calculate(payload).score == calculator.calculate(payload).score


def test_scores_stay_within_range():
    calculator = PriorityCalculator()
    extreme = calculator.calculate(
        {
            "incident_type": "flood",
            "people_reported_affected": 1_000_000,
            "people_displaced": 1_000_000,
            "location": "Zone A",
        }
    )
    assert 0.0 <= extreme.score <= 100.0


def test_every_factor_is_explained():
    """A score you cannot interrogate is a score nobody can trust."""
    result = PriorityCalculator().calculate(
        {
            "incident_type": "flood",
            "people_reported_affected": 40,
            "location": "Zone A",
        }
    )
    assert result.factors
    for factor in result.factors:
        assert factor.label
        assert factor.evidence is not None
        assert factor.weight > 0