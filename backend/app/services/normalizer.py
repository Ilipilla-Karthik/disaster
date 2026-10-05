"""Incident normalisation and canonical ID generation (Requirement 2).

All information sources - emergency calls, field teams, sensors, government
alerts, citizen reports - are converted into one canonical shape so downstream
agents only ever see a single incident schema.

Two distinct jobs live here:

1. ``IncidentNormalizer`` - structural normalisation of already-parsed data,
   plus deterministic ID generation and missing-field detection.
2. ``extract_from_text`` - rule-based extraction used when the LLM is disabled
   or unavailable. It deliberately only extracts what is explicitly stated and
   reports everything else as missing rather than guessing.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models.enums import (
    Confidence,
    IncidentType,
    SourceType,
    VerificationStatus,
)

__all__ = [
    "IncidentNormalizer",
    "extract_from_text",
    "next_incident_id",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
]

# Fields that must be resolved before an incident can be dispatched against.
REQUIRED_FIELDS = ["incident_type", "location", "reported_at"]
# Fields that materially affect priority but may legitimately be unknown.
OPTIONAL_FIELDS = [
    "people_reported_affected",
    "latitude",
    "longitude",
    "infrastructure_issue",
    "assistance_requested",
    "description",
]

_SOURCE_LABELS = {
    SourceType.EMERGENCY_CALL: "emergency_call",
    SourceType.FIELD_TEAM: "field_team",
    SourceType.WEATHER_SERVICE: "weather_service",
    SourceType.IOT_SENSOR: "iot_sensor",
    SourceType.GOVERNMENT_ALERT: "government_alert",
    SourceType.GIS_SYSTEM: "gis_system",
    SourceType.SHELTER: "shelter",
    SourceType.HOSPITAL: "hospital",
    SourceType.RESCUE_TEAM: "rescue_team",
    SourceType.TRANSPORT_AUTHORITY: "transport_authority",
    SourceType.CITIZEN_REPORT: "citizen_report",
    SourceType.MANUAL: "manual",
}


async def next_incident_id(session: Any, prefix: str = "INC", width: int = 3) -> str:
    """Generate the next sequential, human-quotable incident ID (INC-001).

    Sequential rather than random because operators read these IDs aloud over
    radio and dictate them back from the field. Uniqueness is additionally
    guaranteed by the ``incident_id`` unique constraint; on the (practically
    impossible) collision we retry with a disambiguating suffix.
    """
    from sqlalchemy import func, select

    from app.models.models import Incident

    existing = await session.scalar(select(func.count()).select_from(Incident))
    candidate = int(existing or 0) + 1

    while True:
        new_id = f"{prefix}-{candidate:0{width}d}"
        clash = await session.scalar(
            select(Incident.id).where(Incident.incident_id == new_id).limit(1)
        )
        if clash is None:
            return new_id
        candidate += 1


def next_local_incident_id(
    reserved: Iterable[str] | None = None,
    prefix: str = "INC",
    width: int = 3,
) -> str:
    """Sequential ID without a database session.

    Used by the intake agent, which normalises reports before persistence. The
    resulting ID is marked ``incident_id_provisional`` and is replaced by
    :func:`next_incident_id` when the incident is actually written, so the
    in-flight value never has to be trusted as permanent.
    """
    taken = {str(r) for r in (reserved or ())}
    candidate = 1
    while True:
        new_id = f"{prefix}-{candidate:0{width}d}"
        if new_id not in taken:
            return new_id
        candidate += 1


class IncidentNormalizer:
    """Converts heterogeneous payloads into the canonical incident dict."""

    def normalize(
        self,
        data: dict[str, Any],
        reserved_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        incident_type = self._coerce_type(data.get("incident_type"))
        location = (data.get("location") or "Unknown").strip() or "Unknown"

        incident_id = data.get("incident_id") or data.get("incidentId")
        provisional = not bool(incident_id)
        if not incident_id:
            incident_id = next_local_incident_id(reserved_ids)

        source_type = data.get("source_type") or SourceType.MANUAL
        if isinstance(source_type, str):
            try:
                source_type = SourceType(source_type)
            except ValueError:
                source_type = SourceType.MANUAL

        normalised = {
            "incident_id": str(incident_id),
            "incident_id_provisional": provisional,
            "incident_type": incident_type,
            "location": location,
            "latitude": _coerce_float(data.get("latitude")),
            "longitude": _coerce_longitude(data.get("longitude")),
            "description": (data.get("description") or "").strip() or None,
            "raw_report": data.get("raw_text") or data.get("raw_report"),
            "people_reported_affected": _coerce_int(
                data.get("people_reported_affected"), default=0
            ),
            "infrastructure_issue": _clean_issue(data.get("infrastructure_issue")),
            "assistance_requested": _clean_assistance(data.get("assistance_requested")),
            "source": data.get("source") or _SOURCE_LABELS.get(source_type, "manual"),
            "source_type": source_type,
            "confidence": self._coerce_confidence(data.get("confidence")),
            "verification_status": VerificationStatus.PENDING,
            "reported_at": _coerce_dt(data.get("reported_at")) or datetime.now(timezone.utc),
        }
        normalised["missing_fields"] = self.missing_fields(normalised)
        normalised["verification_required"] = bool(normalised["missing_fields"])
        return normalised

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def missing_fields(incident: dict[str, Any]) -> list[str]:
        """List fields that are absent or unverified but priority-relevant."""
        missing: list[str] = []
        if not incident.get("incident_type") or incident["incident_type"] == IncidentType.OTHER:
            missing.append("incident_type")
        if not incident.get("location") or incident.get("location") == "Unknown":
            missing.append("location")
        if incident.get("latitude") is None or incident.get("longitude") is None:
            missing.append("coordinates")
        if not incident.get("people_reported_affected"):
            missing.append("people_reported_affected")
        return missing

    @staticmethod
    def _coerce_type(value: Any) -> IncidentType:
        if isinstance(value, IncidentType):
            return value
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "flash_flood": IncidentType.FLOOD,
            "flooding": IncidentType.FLOOD,
            "storm": IncidentType.CYCLONE,
            "hurricane": IncidentType.CYCLONE,
            "typhoon": IncidentType.CYCLONE,
            "quake": IncidentType.EARTHQUAKE,
            "fire": IncidentType.WILDFIRE,
            "forest_fire": IncidentType.WILDFIRE,
            "industrial": IncidentType.INDUSTRIAL_ACCIDENT,
            "chemical_spill": IncidentType.INDUSTRIAL_ACCIDENT,
            "gas_leak": IncidentType.INDUSTRIAL_ACCIDENT,
        }
        if text in aliases:
            return aliases[text]
        try:
            return IncidentType(text)
        except ValueError:
            return IncidentType.OTHER

    @staticmethod
    def _coerce_confidence(value: Any) -> Confidence:
        if isinstance(value, Confidence):
            return value
        text = str(value or "").strip().lower()
        try:
            return Confidence(text)
        except ValueError:
            return Confidence.MEDIUM


# ---------------------------------------------------------------------------
# Rule-based extraction from unstructured text
# ---------------------------------------------------------------------------

_TYPE_KEYWORDS: list[tuple[str, IncidentType]] = [
    ("industrial accident", IncidentType.INDUSTRIAL_ACCIDENT),
    ("gas leak", IncidentType.INDUSTRIAL_ACCIDENT),
    ("chemical spill", IncidentType.INDUSTRIAL_ACCIDENT),
    ("chemical fire", IncidentType.INDUSTRIAL_ACCIDENT),
    ("oil spill", IncidentType.INDUSTRIAL_ACCIDENT),
    ("explosion", IncidentType.INDUSTRIAL_ACCIDENT),
    ("earthquake", IncidentType.EARTHQUAKE),
    ("quake", IncidentType.EARTHQUAKE),
    ("seismic", IncidentType.EARTHQUAKE),
    ("aftershock", IncidentType.EARTHQUAKE),
    ("landslide", IncidentType.LANDSLIDE),
    ("landslip", IncidentType.LANDSLIDE),
    ("mudslide", IncidentType.LANDSLIDE),
    ("cyclone", IncidentType.CYCLONE),
    ("hurricane", IncidentType.CYCLONE),
    ("typhoon", IncidentType.CYCLONE),
    ("storm surge", IncidentType.CYCLONE),
    ("wildfire", IncidentType.WILDFIRE),
    ("forest fire", IncidentType.WILDFIRE),
    ("bush fire", IncidentType.WILDFIRE),
    ("grass fire", IncidentType.WILDFIRE),
    # The controlled vocabulary has no urban/structural fire category, so a
    # building fire cannot be mapped to a hazard type without a human decision.
    # It is reported as "other" with the hazard left unresolved rather than being
    # mislabelled as a wildfire.
    ("fire", IncidentType.OTHER),
    ("blaze", IncidentType.OTHER),
    ("burning down", IncidentType.OTHER),
    # "rising water" / "water level rising" are flood reports in the field; they
    # are listed after wildfire so a wildfire is never reclassified as a flood.
    ("flood", IncidentType.FLOOD),
    ("flooding", IncidentType.FLOOD),
    ("flooded", IncidentType.FLOOD),
    ("inundation", IncidentType.FLOOD),
    ("inundated", IncidentType.FLOOD),
    ("waterlogging", IncidentType.FLOOD),
    ("waterlogged", IncidentType.FLOOD),
    ("submerged", IncidentType.FLOOD),
    ("overflowing", IncidentType.FLOOD),
    ("rising water", IncidentType.FLOOD),
    ("water rising", IncidentType.FLOOD),
    ("water level", IncidentType.FLOOD),
    ("high water", IncidentType.FLOOD),
    ("waters rising", IncidentType.FLOOD),
    ("flood water", IncidentType.FLOOD),
]

_LOCATION_RE = re.compile(
    r"\b(?:zone|sector|district|village|area|town|block|colony|sector)\s+"
    r"[a-z0-9][a-z0-9\-_]*",
    re.IGNORECASE,
)

_PEOPLE_NOUNS = (
    r"people|persons|residents|families|households|individuals|villagers|citizens|"
    r"students|patients|children|adults|workers|staff|crew|passengers|tourists|"
    r"visitors|stranded"
)

_PEOPLE_PATTERNS = [
    re.compile(
        rf"\b(?:approximately|about|around|roughly|over|nearly|some)?\s*(\d{{1,7}})\s+"
        rf"(?:{_PEOPLE_NOUNS})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(\d{{1,7}})\s+(?:{_PEOPLE_NOUNS})\s+"
        r"(?:are |is |were |was |have been |has been )?(?:affected|stranded|trapped|"
        r"injured|displaced|missing|evacuat|unaccounted|homeless)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,7})\s+(?:households|homes|houses|shops|bungalows)\b",
        re.IGNORECASE,
    ),
]

_INFRA_KEYWORDS: list[tuple[str, str]] = [
    # Specific/partial conditions must be tested before the general road rule,
    # otherwise "partially flooded" is swallowed by "road ... flooded".
    (r"\bpartially\s+(?:blocked|flooded|damaged|passable)\b", "road_partially_blocked"),
    (r"\broad[s]?\s+(?:is\s+|are\s+|has\s+|have\s+)?(?:now\s+)?closed\b", "road_closed"),
    (r"\bbridge\s+(?:collapsed|washed|damaged|down)\b", "bridge_damaged"),
    (r"\broad[s]?\b[^.]*?\b(?:blocked|flooded|washed|submerged|impassable)\b",
     "road_blocked"),
    (r"\b(?:power|electricity)\s+(?:failure|outage|cut|failed)\b", "power_failure"),
    (r"\b(?:water|supply)\s+(?:failure|disruption|contamination)\b", "water_supply_failure"),
    (r"\b(?:school|hospital)\b[^.]*\b(?:damaged|evacuated|affected|flooded)\b",
     "critical_facility_affected"),
    (r"\b(?:building|house|wall|roof|collapse|collapsed)\b[^.]*\b(?:collapsed|damaged)\b",
     "structural_damage"),
    (r"\b(?:structural|building|apartment|shop)\s+fire\b|\bfire\b[^.]*\b(?:building|house|"
     r"apartment|shop)\b",
     "structural_fire"),
    (r"\b(?:landslide|landslip)\b", "landslide_damage"),
]

_ASSISTANCE_KEYWORDS: list[tuple[str, str]] = [
    (r"\bevacuat(?:e|ion|ing)\b", "evacuation"),
    (r"\brescue\b", "rescue"),
    (r"\bsearch and rescue\b|\bsar\b", "search_and_rescue"),
    # "food", "supplies", "rations" or explicitly potable water only - the bare
    # word "water" also occurs in "flood water", which must not match.
    (r"\bfood\b|\bsupplies\b|\brations\b|\bdrinking water\b|\bclean water\b"
     r"|\bwater (?:supply|shortage)\b",
     "food_and_water"),
    (r"\b(?:first aid|medical|ambulance|medical team)\b", "medical_assistance"),
    (r"\b(?:shelter|sheltering|temporary shelter)\b", "shelter"),
    (r"\b(?:boat|boats)\b", "boat_rescue"),
    (r"\b(?:pump|pumping|dewater)\b", "dewatering"),
]

_LOCATION_ASSIGNMENTS = {
    "emergency_call": SourceType.EMERGENCY_CALL,
    "field_team": SourceType.FIELD_TEAM,
    "weather_service": SourceType.WEATHER_SERVICE,
    "iot_sensor": SourceType.IOT_SENSOR,
    "government_alert": SourceType.GOVERNMENT_ALERT,
    "gis_system": SourceType.GIS_SYSTEM,
    "shelter": SourceType.SHELTER,
    "hospital": SourceType.HOSPITAL,
    "rescue_team": SourceType.RESCUE_TEAM,
    "transport_authority": SourceType.TRANSPORT_AUTHORITY,
    "citizen_report": SourceType.CITIZEN_REPORT,
    "manual": SourceType.MANUAL,
}


_AMBIGUOUS_TYPE_KEYWORDS: set[str] = {"fire", "blaze", "burning down"}


def extract_from_text(text: str, source: str = "unstructured") -> dict[str, Any]:
    """Deterministic keyword extraction for unstructured reports.

    Only explicitly stated facts are returned. Anything not found is left as
    ``None`` so the pipeline reports it as ``Verification Required`` instead of
    inventing a value.
    """
    if not text:
        return {}

    lowered = text.lower()

    incident_type: IncidentType | None = None
    type_ambiguous = False
    for keyword, itype in _TYPE_KEYWORDS:
        if keyword in lowered:
            incident_type = itype
            type_ambiguous = keyword in _AMBIGUOUS_TYPE_KEYWORDS
            break

    location: str | None = None
    m = _LOCATION_RE.search(text)
    if m:
        location = re.sub(r"\s+", " ", m.group(0)).strip()

    people: int | None = None
    for pattern in _PEOPLE_PATTERNS:
        found = pattern.search(text)
        if found:
            try:
                people = int(found.group(1))
            except (TypeError, ValueError):
                people = None
            break

    infrastructure: str | None = None
    for pattern, label in _INFRA_KEYWORDS:
        if re.search(pattern, lowered):
            infrastructure = label
            break

    assistance: list[str] = []
    for pattern, label in _ASSISTANCE_KEYWORDS:
        if re.search(pattern, lowered):
            assistance.append(label)

    # "20 houses flooded" describes dwellings, not people; we do not convert it
    # into a population figure because occupancy per dwelling is unknown.
    try:
        source_type = _LOCATION_ASSIGNMENTS.get(str(source).lower(), SourceType.MANUAL)
    except Exception:
        source_type = SourceType.MANUAL

    extracted = {
        "incident_type": incident_type,
        "location": location,
        "people_reported_affected": people,
        "infrastructure_issue": infrastructure,
        "assistance_requested": ", ".join(assistance) if assistance else None,
        "source": source,
        "source_type": source_type,
        "raw_text": text,
"extraction_method": "deterministic_keywords",
        "unresolved": [
            name
            for name, value in (
                ("incident_type", None if type_ambiguous else incident_type),
                ("location", location),
                ("people_reported_affected", people),
                ("assistance_requested", ", ".join(assistance) if assistance else None),
            )
            if value is None
        ],
    }
    return extracted


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    if -90 <= f <= 90:
        return f
    return None


def _coerce_longitude(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    if -180 <= f <= 180:
        return f
    return None


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clean_issue(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower().replace(" ", "_")
    return text or None


def _clean_assistance(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v).strip().lower().replace(" ", "_") for v in value) or None
    return str(value).strip().lower().replace(" ", "_") or None