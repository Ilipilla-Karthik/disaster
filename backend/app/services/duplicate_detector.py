"""Duplicate / related incident detection (Requirement 3).

Methodology
-----------
Weighted similarity across five independent signals:

    similarity = 0.25 * type_match
               + 0.25 * location_match
               + 0.20 * proximity_match
               + 0.15 * temporal_match
               + 0.15 * text_similarity

``type_match``/``location_match`` are boolean, ``proximity_match`` decays
linearly to zero at ``settings.DUPLICATE_RADIUS_KM``, ``temporal_match`` decays
to zero at ``settings.DUPLICATE_WINDOW_HOURS``, and ``text_similarity`` uses
token Jaccard similarity (no external dependency, deterministic).

Safety behaviour
----------------
Potential duplicates are **linked for human review, never deleted or merged**.
The lower-confidence report is marked ``is_duplicate`` with a pointer to the
canonical incident and a similarity score, and a ``duplicate_detected`` alert is
raised. An authorised coordinator decides whether to keep them separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from app.core.config import settings
from app.core.constants import DUPLICATE_WEIGHTS
from app.core.utils import enum_value

__all__ = ["DuplicateDetector", "DuplicateMatch", "tokenise", "jaccard"]

# Placeholder written by the normaliser when no location could be resolved.
_UNKNOWN_LOCATION = "unknown"

# Minimum description similarity required to link two reports that share no
# place information. Below this, matching on hazard type + timing alone is not
# enough evidence that two reports describe the same event.
_MIN_TEXT_WITHOUT_LOCATION = 0.30

_WORD_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "on", "to", "for", "is",
    "are", "was", "were", "has", "have", "had", "with", "from", "by", "it",
    "this", "that", "there", "near", "approximately", "about", "around",
    "reported", "people", "residents", "please", "help", "reported",
}


def tokenise(text: str | None) -> set[str]:
    """Lowercase alphanumeric tokens with stopwords removed."""
    if not text:
        return set()
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class DuplicateMatch:
    incident_id: str
    duplicate_of: str
    similarity: float
    matched_on: list[str]
    requires_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "duplicate_of": self.duplicate_of,
            "similarity": round(self.similarity, 4),
            "matched_on": self.matched_on,
            "requires_review": self.requires_review,
        }


class DuplicateDetector:
    """Stateless similarity engine for potential duplicate reports."""

    def __init__(
        self,
        threshold: float | None = None,
        radius_km: float | None = None,
        window_hours: int | None = None,
    ) -> None:
        self.threshold = (
            threshold if threshold is not None else settings.DUPLICATE_SIMILARITY_THRESHOLD
        )
        self.radius_km = (
            radius_km if radius_km is not None else settings.DUPLICATE_RADIUS_KM
        )
        self.window_hours = (
            window_hours if window_hours is not None else settings.DUPLICATE_WINDOW_HOURS
        )

    # -- individual signals -------------------------------------------------

    @staticmethod
    def _type_match(new: dict[str, Any], existing: dict[str, Any]) -> tuple[float, str]:
        # Normalised records carry an IncidentType enum while ORM rows and API
        # payloads carry plain strings; compare the value, not repr().
        a = enum_value(new.get("incident_type"))
        b = enum_value(existing.get("incident_type"))
        if not a or not b:
            return 0.0, "type_missing"
        return (1.0, "incident_type") if a == b else (0.0, "type_mismatch")

    @staticmethod
    def _location_match(new: dict[str, Any], existing: dict[str, Any]) -> tuple[float, str]:
        a = str(new.get("location") or "").strip().lower()
        b = str(existing.get("location") or "").strip().lower()
        # "Unknown" is a placeholder for *no* location information. Two reports
        # that both lack a location have not been shown to be co-located.
        if not a or not b or a == _UNKNOWN_LOCATION or b == _UNKNOWN_LOCATION:
            return 0.0, "location_missing"
        if a == b:
            return 1.0, "location_exact"
        if a in b or b in a:
            return 0.9, "location_contains"
        if jaccard(tokenise(a), tokenise(b)) >= 0.6:
            return 0.7, "location_similar"
        return 0.0, "location_mismatch"

    def _proximity_match(
        self, new: dict[str, Any], existing: dict[str, Any]
    ) -> tuple[float, str]:
        from app.services.geo import haversine_km

        lat1, lon1 = new.get("latitude"), new.get("longitude")
        lat2, lon2 = existing.get("latitude"), existing.get("longitude")
        if None in (lat1, lon1, lat2, lon2):
            return 0.0, "coordinates_missing"
        dist = haversine_km(float(lat1), float(lon1), float(lat2), float(lon2))
        if dist > self.radius_km:
            return 0.0, f"distance_{dist:.2f}km_exceeds_{self.radius_km}km"
        score = 1.0 - (dist / self.radius_km)
        return max(0.0, score), f"within_{dist:.2f}km"

    def _temporal_match(
        self, new: dict[str, Any], existing: dict[str, Any]
    ) -> tuple[float, str]:
        t_new = _as_dt(new.get("reported_at")) or datetime.now(timezone.utc)
        t_old = _as_dt(existing.get("reported_at")) or datetime.now(timezone.utc)
        delta = abs((t_new - t_old).total_seconds()) / 3600.0
        if delta > self.window_hours:
            return 0.0, f"gap_{delta:.1f}h_exceeds_{self.window_hours}h"
        score = 1.0 - (delta / self.window_hours)
        return max(0.0, score), f"within_{delta:.1f}h"

    @staticmethod
    def _text_match(new: dict[str, Any], existing: dict[str, Any]) -> tuple[float, str]:
        a = tokenise(new.get("description") or new.get("raw_report"))
        b = tokenise(existing.get("description") or existing.get("raw_report"))
        if not a or not b:
            return 0.0, "description_missing"
        score = jaccard(a, b)
        if score <= 0.0:
            return 0.0, "description_disjoint"
        return score, f"description_similarity_{score:.2f}"

    # -- public API ---------------------------------------------------------

    def similarity(self, new: dict[str, Any], existing: dict[str, Any]) -> tuple[float, list[str]]:
        """Return (0-1 similarity, list of signals that contributed)."""
        components = {
            "type": self._type_match(new, existing),
            "location": self._location_match(new, existing),
            "proximity": self._proximity_match(new, existing),
            "temporal": self._temporal_match(new, existing),
            "text": self._text_match(new, existing),
        }
        score = sum(DUPLICATE_WEIGHTS[k] * v[0] for k, v in components.items())

        # If the signals that could not be evaluated are only *missing* (no
        # location, no coordinates), their weight is redistributed across the
        # signals that were evaluated. Otherwise a report with an unresolved
        # location could never reach the threshold, and a second report about
        # the same event from the same zone would silently create two incidents.
        unavailable = {
            "location",
            "proximity",
        } & {k for k, v in components.items() if v[0] == 0.0 and v[1].endswith("_missing")}
        if unavailable and score > 0.0:
            # Renormalising raises the score, so it must not be allowed to turn
            # a weak match into a strong one. When no place information exists,
            # the description is the only independent evidence - if that is
            # thin, these are not established as the same event.
            text_score = components["text"][0]
            if "location" in unavailable and text_score < _MIN_TEXT_WITHOUT_LOCATION:
                return 0.0, ["insufficient_evidence_without_location"]
            available_weight = sum(
                DUPLICATE_WEIGHTS[k] for k in DUPLICATE_WEIGHTS if k not in unavailable
            )
            evaluated = {
                k: DUPLICATE_WEIGHTS[k] * v[0]
                for k, v in components.items()
                if k not in unavailable
            }
            score = sum(evaluated.values()) / available_weight

        # A different hazard type is disqualifying: a flood report is never a
        # duplicate of an earthquake report regardless of wording overlap.
        if components["type"][0] == 0.0 and components["type"][1] == "type_mismatch":
            score *= 0.35

        matched = [
            v[1]
            for k, v in components.items()
            if v[0] > 0.0 and v[1] not in {"location_missing", "coordinates_missing",
                                           "description_missing", "type_missing"}
        ]
        return round(min(score, 1.0), 4), matched

    def find_duplicates(
        self,
        new_incident: dict[str, Any],
        existing_incidents: Iterable[Any],
    ) -> list[DuplicateMatch]:
        """All existing incidents that look like duplicates of ``new_incident``.

        Accepts ORM objects or plain dicts. Results are sorted by descending
        similarity and filtered by the configured threshold.
        """
        new_dict = _as_dict(new_incident)
        new_id = new_dict.get("incident_id")
        candidates = [_as_dict(e) for e in existing_incidents]

        # A cluster is linked as a flat fan around one canonical incident, so a
        # report matching a report that is already a duplicate still resolves to
        # the same parent instead of forming a chain. Candidates that are
        # themselves flagged are therefore scored, not skipped: excluding them
        # means the third call about one event is never matched at all.
        parent_of = {
            c["incident_id"]: c.get("duplicate_of")
            for c in candidates
            if c.get("incident_id")
        }

        def canonical(incident_id: str | None) -> str | None:
            seen: set[str] = set()
            current = incident_id
            while current and current not in seen:
                seen.add(current)
                nxt = parent_of.get(current)
                if not nxt or nxt == current:
                    return current
                current = nxt
            return current

        matches: list[DuplicateMatch] = []
        best_for_parent: dict[str, DuplicateMatch] = {}
        for ex_dict in candidates:
            ex_id = ex_dict.get("incident_id")
            if not ex_id or ex_id == new_id:
                continue

            score, signals = self.similarity(new_dict, ex_dict)
            if score < self.threshold:
                continue

            parent = canonical(ex_id)
            if not parent or parent == new_id:
                continue

            match = DuplicateMatch(
                incident_id=new_id or "",
                duplicate_of=parent,
                similarity=score,
                matched_on=signals,
            )
            previous = best_for_parent.get(parent)
            if previous is None or match.similarity > previous.similarity:
                best_for_parent[parent] = match

        matches = list(best_for_parent.values())
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches

    def detect(
        self,
        new_incident: dict[str, Any],
        existing_incidents: Sequence[Any],
    ) -> tuple[bool, str | None, float | None]:
        """Convenience wrapper returning (is_duplicate, canonical_id, score)."""
        matches = self.find_duplicates(new_incident, existing_incidents)
        if matches:
            best = matches[0]
            return True, best.duplicate_of, best.similarity
        return False, None, None


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    keys = (
        "incident_id", "incident_type", "location", "latitude", "longitude",
        "description", "reported_at", "duplicate_of",
    )
    return {k: getattr(obj, k, None) for k in keys}