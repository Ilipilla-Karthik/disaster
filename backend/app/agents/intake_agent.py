"""Agent 1 - Emergency Incident Intake Agent.

Collects and structures incoming emergency information from heterogeneous
sources, extracts the key fields, flags what is missing, and detects potential
duplicate reports (which are linked for review, never deleted).

Two extraction paths, in order of preference:

1. **LLM extraction** when a provider is configured - handles messy, colloquial
   multilingual free text better than rules can.
2. **Deterministic keyword extraction** otherwise. The system must work with no
   API key, and must never let a language model's guess become a "fact".

Both paths are validated against the same controlled vocabulary, and anything
not confidently extracted is returned in ``unresolved`` so the incident is
marked *Verification Required* instead of being completed with invented data.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import AgentError, BaseAgent
from app.orchestration.state import WorkflowState
from app.services.duplicate_detector import DuplicateDetector
from app.services.normalizer import IncidentNormalizer, extract_from_text
from app.tools.geocoding_tool import geocode
from app.tools.llm_tool import get_llm

logger = logging.getLogger(__name__)

__all__ = ["IncidentIntakeAgent"]

VALID_TYPES = {
    "flood", "cyclone", "earthquake", "wildfire", "landslide",
    "industrial_accident", "other",
}


class IncidentIntakeAgent(BaseAgent):
    name = "emergency_incident_intake"
    role = "Collect and structure incoming emergency information"
    node_name = "intake_output"
    uses_llm = True

    system_prompt = """
You are the Emergency Incident Intake Agent for a disaster response system.

Extract structured facts from an incoming emergency report. The report may be
an emergency call transcript, a field-team update, a government alert, a
sensor event or a citizen message.

Return ONLY these keys:
  incident_type  - exactly one of: flood, cyclone, earthquake, wildfire,
                   landslide, industrial_accident, other
  location       - the named place (e.g. "Zone A", "Sector D")
  people_reported_affected - an integer, ONLY if a population figure is
                   explicitly stated. Use null if not stated.
  infrastructure_issue - short snake_case label, or null
  assistance_requested  - comma-separated snake_case labels, or null
  unresolved     - array of field names you could NOT determine from the text

Rules you must follow:
- Never infer or estimate a number that is not stated in the text.
- Never guess a location that is not named in the text.
- If the hazard type is not stated, use "other" and list "incident_type"
  in unresolved.
- Do not add commentary, explanation, or any key not listed above.
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        payloads = state.get("incident_payloads") or []
        existing = state.get("incidents") or []

        normalized: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        llm_used = False
        notes: list[str] = []

        for payload in payloads:
            record = await self._intake_one(payload, existing + normalized, notes)
            record["_llm_used"] = record.get("extraction_method") == "llm"
            llm_used = llm_used or record["_llm_used"]
            record.pop("_llm_used", None)
            normalized.append(record)

            for link in record.pop("duplicate_links", []):
                links.append(link)

        if payloads:
            notes.append(
                f"Intake processed {len(payloads)} report(s); "
                f"{len(links)} potential duplicate link(s) raised for human review."
            )

        missing_all = sorted({f for r in normalized for f in r.get("missing_fields", [])})

        return {
            "normalized_incidents": normalized,
            "duplicate_links": links,
            "intake_output": {
                "reports_processed": len(payloads),
                "extraction_methods": sorted(
                    {r.get("extraction_method", "structured") for r in normalized}
                ),
                "duplicates_detected": len(links),
                "missing_fields": missing_all,
                "verification_required": bool(missing_all),
                "notes": notes,
            },
            "_llm_used": llm_used,
        }

    # -- one report ---------------------------------------------------------

    async def _intake_one(
        self,
        payload: dict[str, Any],
        existing: list[dict[str, Any]],
        notes: list[str],
    ) -> dict[str, Any]:
        raw_text = payload.get("raw_text")
        # An operator-typed description is free text too: run it through the
        # same extraction path so "Flood in Zone A" does not land as Unknown.
        extraction_text = raw_text or payload.get("description")
        normalizer = IncidentNormalizer()

        extracted: dict[str, Any] = {}
        if extraction_text:
            llm = get_llm()
            result = await llm.complete_json(
                self.system_prompt,
                {"report_text": extraction_text, "source": payload.get("source")},
                fallback_payload=extract_from_text(
                    extraction_text, str(payload.get("source") or "")
                ),
                schema_hint=(
                    '{"incident_type": str, "location": str|null, '
                    '"people_reported_affected": int|null, '
                    '"infrastructure_issue": str|null, "assistance_requested": str|null, '
                    '"unresolved": [str]}'
                ),
            )
            extracted = result.data or {}
            method = "llm" if result.used_llm else "deterministic_keywords"
            if not result.used_llm and result.error not in {
                "llm_not_configured", None
            }:
                notes.append(
                    f"LLM extraction unavailable ({result.error}); "
                    "used deterministic keyword extraction."
                )
            extracted["extraction_method"] = method
            extracted["raw_text"] = extraction_text
        else:
            extracted = {"extraction_method": "structured"}
            method = "structured"

        # Explicit structured values always win over extracted ones.
        merged: dict[str, Any] = {
            "incident_type": payload.get("incident_type") or extracted.get("incident_type"),
            "location": payload.get("location") or extracted.get("location"),
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "description": payload.get("description"),
            "people_reported_affected": (
                payload.get("people_reported_affected")
                if payload.get("people_reported_affected") not in (None, "")
                else extracted.get("people_reported_affected")
            ),
            "infrastructure_issue": (
                payload.get("infrastructure_issue") or extracted.get("infrastructure_issue")
            ),
            "assistance_requested": (
                payload.get("assistance_requested") or extracted.get("assistance_requested")
            ),
            "source": payload.get("source"),
            "source_type": payload.get("source_type"),
            "confidence": payload.get("confidence"),
            "reported_at": payload.get("reported_at"),
            "extraction_method": method,
            "unresolved": extracted.get("unresolved") or [],
        }

        record = normalizer.normalize(
            merged,
            reserved_ids=[r.get("incident_id") for r in existing if r.get("incident_id")],
        )
        # normalize() rebuilds the record from the model fields, so provenance
        # about *how* we got here has to be re-attached explicitly.
        record["extraction_method"] = method
        record["unresolved"] = list(extracted.get("unresolved") or [])

        # Anything the extractor could not determine is explicitly unresolved.
        for field in extracted.get("unresolved") or []:
            if field not in record["missing_fields"]:
                record["missing_fields"].append(field)

        record["description"] = (
            payload.get("description") or extraction_text or record.get("description")
        )

        # ---- geocode when no coordinates were supplied -------------------
        if record.get("latitude") is None or record.get("longitude") is None:
            place = record.get("location")
            if place and place != "Unknown":
                resolved = await geocode(place)
                if resolved.get("latitude") is not None:
                    record["latitude"] = resolved["latitude"]
                    record["longitude"] = resolved["longitude"]
                    record["geocode"] = {
                        "source": resolved.get("source"),
                        "verified": resolved.get("verified"),
                        "note": resolved.get("note"),
                    }
                    if not resolved.get("verified"):
                        record["missing_fields"].append("coordinates")
                        notes.append(
                            f"Coordinates for '{place}' resolved from "
                            f"{resolved.get('source')} and require field verification."
                        )
                else:
                    record["geocode"] = {
                        "source": resolved.get("source"),
                        "note": resolved.get("note"),
                    }
                    notes.append(
                        f"Location '{place}' could not be geocoded; the incident will "
                        "have no map position."
                    )
            elif "location" not in record["missing_fields"]:
                record["missing_fields"].append("location")
        if "coordinates" in record["missing_fields"]:
            record["verification_required"] = True
        # Deduplicate while preserving order: the same field can be flagged by
        # the extractor, by the normaliser and by the geocoder.
        record["missing_fields"] = list(dict.fromkeys(record["missing_fields"]))

        record["reported_at"] = record.get("reported_at")
        record["_db_id"] = payload.get("_db_id")

        # Duplicate detection against everything already known.
        detector = DuplicateDetector()
        matches = detector.find_duplicates(record, existing)
        record["duplicate_links"] = [m.to_dict() for m in matches]
        if matches:
            best = matches[0]
            record["is_duplicate"] = True
            record["duplicate_of"] = best.duplicate_of
            record["duplicate_similarity"] = best.similarity
            notes.append(
                f"Potential duplicate of {best.duplicate_of} "
                f"(similarity {best.similarity:.2f}) - linked for human review, "
                "not deleted or auto-merged."
            )

        return record