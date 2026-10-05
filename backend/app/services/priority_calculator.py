"""Deterministic incident priority assessment (Requirement 4).

Design contract
---------------
* Every number here is produced by pure arithmetic over *verified input data*.
  No LLM call participates in scoring.
* Each contributing factor is returned alongside its raw value, its weight and
  the evidence used, so the UI/API can always explain a result.
* Unknown or missing inputs lower the ``uncertainty`` factor and mark fields
  as ``Verification Required`` instead of being guessed.

Score = sum(weight_i * normalised_value_i), clamped to [0, 100].
Severity is then read off documented band boundaries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.constants import (
    INCIDENT_TYPE_RISK,
    PRIORITY_FACTOR_CAPS,
    PRIORITY_WEIGHTS,
    ROUTE_STATUS_IMPACT,
    SEVERITY_BANDS,
    WEATHER_DETERIORATION_WEIGHTS,
    WEATHER_MAX,
)
from app.models.enums import RouteStatus, SeverityLevel

__all__ = [
    "PriorityCalculator",
    "PriorityResult",
    "Factor",
    "people_affected_factor",
    "incident_type_factor",
    "access_factor",
    "weather_factor",
    "infrastructure_factor",
    "response_gap_factor",
    "uncertainty_factor",
    "escalation_factor",
]


@dataclass
class Factor:
    """One transparent contributor to the priority score."""

    factor: str
    label: str
    raw_value: Any
    normalised: float  # 0.0 - 1.0
    weight: float
    contribution: float  # weight * normalised, capped
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "label": self.label,
            "raw_value": self.raw_value,
            "normalised": round(self.normalised, 4),
            "weight": self.weight,
            "contribution": round(self.contribution, 2),
            "evidence": self.evidence,
        }


@dataclass
class PriorityResult:
    score: float
    severity: SeverityLevel
    factors: list[Factor] = field(default_factory=list)
    verification_required_fields: list[str] = field(default_factory=list)
    escalation_potential: str = "none"
    rationale: str = ""
    weather_component: dict[str, Any] = field(default_factory=dict)
    access_component: dict[str, Any] = field(default_factory=dict)
    # Band-derived severity before the verification gate is applied. Retained so
    # the UI can show "HIGH -> Critical Review Required" without losing detail.
    unverified_severity: SeverityLevel = SeverityLevel.LOW
    requires_verification: bool = False

    @property
    def contributions(self) -> dict[str, float]:
        return {f.factor: round(f.contribution, 2) for f in self.factors}

    def to_factors_payload(self) -> dict[str, Any]:
        """Shape persisted into ``Incident.priority_factors``."""
        return {
            "score": round(self.score, 2),
            "severity": self.severity.value,
            "unverified_severity": self.unverified_severity.value,
            "requires_verification": self.requires_verification,
            "methodology": (
                "Deterministic weighted factor model. "
                "score = sum(weight * normalised_value), clamped to 0-100."
            ),
            "weights": PRIORITY_WEIGHTS,
            "factors": [f.to_dict() for f in self.factors],
            "verification_required_fields": self.verification_required_fields,
            "escalation_potential": self.escalation_potential,
            "rationale": self.rationale,
            "band_thresholds": {band: thr for thr, band in SEVERITY_BANDS},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Individual factor functions
# ---------------------------------------------------------------------------


def people_affected_factor(people: int | None) -> Factor:
    """Log-scaled threat-to-life factor.

    1 -> ~0.12, 10 -> ~0.50, 50 -> ~0.85, 100 -> ~1.0.
    Log scaling prevents a single large count from dominating every other
    consideration, which is the failure mode the specification warns about
    ("should not simply send all resources to the incident with the highest
    reported number of affected people").
    """
    people = int(people or 0)
    normalised = _clamp01(math.log10(people + 1) / 2.0) if people > 0 else 0.0
    weight = PRIORITY_WEIGHTS["people_affected"]
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["people_affected"])
    return Factor(
        factor="people_affected",
        label="People reported affected",
        raw_value=people,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=(
            f"{people} people reported affected by the source. "
            "Figure is self-reported and unverified."
            if people
            else "No population impact reported - contribution is zero, not assumed safe."
        ),
    )


def incident_type_factor(incident_type: str | None) -> Factor:
    key = (incident_type or "other").lower()
    risk = INCIDENT_TYPE_RISK.get(key, INCIDENT_TYPE_RISK["other"])
    weight = PRIORITY_WEIGHTS["incident_type_risk"]
    contribution = min(weight * risk, PRIORITY_FACTOR_CAPS["incident_type_risk"])
    return Factor(
        factor="incident_type_risk",
        label="Hazard lethality / time-criticality",
        raw_value=key,
        normalised=risk,
        weight=weight,
        contribution=contribution,
        evidence=(
            f"'{key}' carries an intrinsic risk coefficient of {risk:.2f} from the "
            "published hazard-type risk table (constants.INCIDENT_TYPE_RISK)."
        ),
    )


def access_factor(access: dict[str, Any] | None) -> Factor:
    """Access restriction - isolation raises priority."""
    weight = PRIORITY_WEIGHTS["access_restriction"]
    if not access:
        return Factor(
            factor="access_restriction",
            label="Accessibility constraint",
            raw_value=None,
            normalised=0.0,
            weight=weight,
            contribution=0.0,
            evidence="No accessibility analysis available - contributes 0. Verification required.",
        )

    status = access.get("status") or access.get("road_status") or RouteStatus.UNKNOWN
    impact = ROUTE_STATUS_IMPACT.get(status, ROUTE_STATUS_IMPACT[RouteStatus.UNKNOWN])
    normalised = _clamp01(impact)

    # An inferred problem is treated as a precaution, never as a confirmed fact.
    if access.get("fact_status") == "inferred":
        normalised *= 0.5

    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["access_restriction"])
    return Factor(
        factor="access_restriction",
        label="Accessibility constraint",
        raw_value=status,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=access.get(
            "evidence",
            f"Primary access route status: {status} (impact coefficient {impact:.2f}).",
        ),
    )


def weather_factor(weather: dict[str, Any] | None) -> Factor:
    """Deteriorating weather raises escalation potential."""
    weight = PRIORITY_WEIGHTS["weather_deterioration"]
    if not weather:
        return Factor(
            factor="weather_deterioration",
            label="Weather deterioration",
            raw_value=None,
            normalised=0.0,
            weight=weight,
            contribution=0.0,
            evidence="No weather data available - contributes 0. Verification required.",
        )

    total = 0.0
    evidence: list[str] = []
    raw: dict[str, Any] = {}

    for key, w in WEATHER_DETERIORATION_WEIGHTS.items():
        if bool(weather.get(key)):
            total += w
            raw[key] = True
            evidence.append(f"{key.replace('_', ' ')} (weight +{w})")

    rainfall = weather.get("rainfall_next_6h_mm")
    if rainfall is not None:
        raw["rainfall_next_6h_mm"] = rainfall
        if rainfall >= 100:
            total += 8
            evidence.append(f"forecast rainfall {rainfall}mm/6h (+8)")
        elif rainfall >= 50:
            total += 5
            evidence.append(f"forecast rainfall {rainfall}mm/6h (+5)")
        elif rainfall >= 25:
            total += 2
            evidence.append(f"forecast rainfall {rainfall}mm/6h (+2)")

    normalised = _clamp01(total / WEATHER_MAX)
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["weather_deterioration"])
    return Factor(
        factor="weather_deterioration",
        label="Weather deterioration risk",
        raw_value=raw,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence="; ".join(evidence) if evidence else "Conditions not deteriorating.",
    )


def infrastructure_factor(incident: dict[str, Any]) -> Factor:
    """Lifeline failure - e.g. road cut, power loss, hospital impact."""
    weight = PRIORITY_WEIGHTS["infrastructure_impact"]
    issue = (incident.get("infrastructure_issue") or "").strip()
    if not issue:
        return Factor(
            factor="infrastructure_impact",
            label="Infrastructure impact",
            raw_value=None,
            normalised=0.0,
            weight=weight,
            contribution=0.0,
            evidence="No infrastructure damage reported.",
        )

    normalised = _clamp01(0.6 if issue else 0.0)
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["infrastructure_impact"])
    return Factor(
        factor="infrastructure_impact",
        label="Infrastructure impact",
        raw_value=issue,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=f"Infrastructure issue reported: '{issue}'.",
    )


def response_gap_factor(assigned_count: int, plan_exists: bool = False) -> Factor:
    """Higher when nothing is deployed yet."""
    weight = PRIORITY_WEIGHTS["response_gap"]
    if assigned_count <= 0:
        normalised = 1.0
        evidence = "No response resource currently assigned to this incident."
    elif assigned_count == 1:
        normalised = 0.5
        evidence = f"{assigned_count} resource assigned; capability gaps may remain."
    else:
        normalised = 0.0
        evidence = f"{assigned_count} resources assigned."
    if plan_exists and assigned_count > 0:
        normalised = max(0.0, normalised - 0.2)
        evidence += " An active response plan exists."
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["response_gap"])
    return Factor(
        factor="response_gap",
        label="Unmet response need",
        raw_value=assigned_count,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=evidence,
    )


def uncertainty_factor(
    confidence: str | None,
    verification_status: str | None,
    missing_fields: list[str] | None,
) -> tuple[Factor, list[str]]:
    """Uncertainty does not lower priority - it marks work as unverified."""
    weight = PRIORITY_WEIGHTS["uncertainty"]
    missing = list(missing_fields or [])
    score = 0.0
    parts: list[str] = []

    if (confidence or "").lower() == "low":
        score += 2.0
        parts.append("source confidence reported as low (+2)")
    if (verification_status or "").lower() in {"pending", "needs_review"}:
        score += 2.0
        parts.append("incident not yet verified (+2)")
    if missing:
        score += min(3.0, len(missing))
        parts.append(f"{len(missing)} field(s) missing or unverified (+{min(3.0, len(missing))})")

    normalised = _clamp01(score / 7.0)
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["uncertainty"])
    factor = Factor(
        factor="uncertainty",
        label="Information uncertainty",
        raw_value={"confidence": confidence, "missing_fields": missing},
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=(
            "; ".join(parts)
            or "Information is complete and confidence is acceptable."
        )
        + " Uncertainty is surfaced as 'Verification Required', not resolved by assumption.",
    )
    return factor, missing


def escalation_factor(incident: dict[str, Any]) -> Factor:
    """Explicit escalation signals in the report."""
    weight = PRIORITY_WEIGHTS["escalation_signal"]
    text = " ".join(
        str(incident.get(k) or "")
        for k in ("description", "assistance_requested", "infrastructure_issue")
    ).lower()

    signals = [
        token
        for token in (
            "trapped",
            "unconscious",
            "missing",
            "child",
            "elderly",
            "injured",
            "stranded",
            "cut off",
            "cut-off",
            "collapse",
            "fire",
            "gas leak",
            "evacuate",
        )
        if token in text
    ]
    normalised = _clamp01(len(signals) / 3.0)
    contribution = min(weight * normalised, PRIORITY_FACTOR_CAPS["escalation_signal"])
    return Factor(
        factor="escalation_signal",
        label="Escalation indicator in report text",
        raw_value=signals,
        normalised=normalised,
        weight=weight,
        contribution=contribution,
        evidence=(
            f"Report text contains escalation indicator(s): {', '.join(signals)}."
            if signals
            else "No escalation keywords detected in report text."
        ),
    )


# ---------------------------------------------------------------------------
# Orchestrating calculator
# ---------------------------------------------------------------------------


class PriorityCalculator:
    """Stateless, side-effect-free priority engine."""

    def calculate(
        self,
        incident: dict[str, Any],
        *,
        weather: dict[str, Any] | None = None,
        accessibility: dict[str, Any] | None = None,
        assigned_resource_count: int = 0,
        plan_exists: bool = False,
    ) -> PriorityResult:
        factors: list[Factor] = []

        factors.append(people_affected_factor(incident.get("people_reported_affected")))
        factors.append(incident_type_factor(incident.get("incident_type")))
        factors.append(access_factor(accessibility))
        factors.append(weather_factor(weather))
        factors.append(infrastructure_factor(incident))
        factors.append(
            response_gap_factor(assigned_resource_count, plan_exists=plan_exists)
        )

        uncertainty, missing = uncertainty_factor(
            incident.get("confidence"),
            incident.get("verification_status"),
            incident.get("missing_fields"),
        )
        factors.append(uncertainty)
        factors.append(escalation_factor(incident))

        raw_total = sum(f.contribution for f in factors)
        score = max(0.0, min(100.0, raw_total))
        band_severity = severity_for_score(score)

        # ---- Verification gate -------------------------------------------
        # The band score alone is never shown as an operational verdict for
        # unverified data. Per the safety requirement, unconfirmed incidents
        # are reported as "Critical Review Required"
        # (SeverityLevel.VERIFICATION_REQUIRED) while the band result is
        # retained for the reviewer.
        is_verified = (incident.get("verification_status") or "").lower() == "verified"
        requires_verification = bool(missing) or not is_verified
        severity = band_severity
        if requires_verification:
            # Applies to every band, including LOW. A low score derived from
            # unverified input is not evidence that nothing is wrong - it is
            # evidence that we do not know. Showing 'low' there would read as
            # an all-clear, which is exactly the failure this gate exists to
            # prevent. The numeric band is kept in ``unverified_severity``.
            severity = SeverityLevel.VERIFICATION_REQUIRED

        escalation_potential = _escalation_potential(weather, accessibility, incident)
        rationale = self._build_rationale(
            score,
            band_severity,
            severity,
            factors,
            missing,
            escalation_potential,
            requires_verification,
        )

        return PriorityResult(
            score=round(score, 2),
            severity=severity,
            factors=factors,
            verification_required_fields=missing,
            escalation_potential=escalation_potential,
            rationale=rationale,
            weather_component=weather or {},
            access_component=accessibility or {},
            unverified_severity=band_severity,
            requires_verification=requires_verification,
        )

    @staticmethod
    def _build_rationale(
        score: float,
        band_severity: SeverityLevel,
        severity: SeverityLevel,
        factors: list[Factor],
        missing: list[str],
        escalation: str,
        requires_verification: bool,
    ) -> str:
        top = sorted(factors, key=lambda f: f.contribution, reverse=True)[:3]
        drivers = "; ".join(
            f"{f.label} (+{f.contribution:.1f})" for f in top if f.contribution > 0
        )
        text = (
            f"Priority {score:.1f}/100 -> band {band_severity.value}. "
            f"Primary drivers: {drivers or 'no significant factor'}."
        )
        if requires_verification and severity != band_severity:
            text += (
                f" Reported as '{severity.value}' (Critical Review Required) because "
                "the incident is not yet verified by an authorised source."
            )
        if escalation != "none":
            text += f" Escalation potential: {escalation}."
        if missing:
            text += f" Verification required for: {', '.join(missing)}."
        return text


def severity_for_score(score: float) -> SeverityLevel:
    for threshold, label in SEVERITY_BANDS:
        if score >= threshold:
            return SeverityLevel(label)
    return SeverityLevel.VERIFICATION_REQUIRED


def _escalation_potential(
    weather: dict[str, Any] | None,
    accessibility: dict[str, Any] | None,
    incident: dict[str, Any],
) -> str:
    """Label how likely the incident is to worsen."""
    access_bad = bool(
        accessibility
        and (accessibility.get("status") or accessibility.get("road_status"))
        in {RouteStatus.CLOSED, RouteStatus.IMPASSABLE}
    )
    weather_bad = bool(
        weather
        and (
            weather.get("worsening")
            or weather.get("severe_warning")
            or (weather.get("rainfall_next_6h_mm") or 0) >= 50
        )
    )
    unassigned = int(incident.get("assigned_resource_count") or 0) == 0

    if access_bad and weather_bad and unassigned:
        return "high - isolated area, deteriorating weather, no resource assigned"
    if weather_bad and access_bad:
        return "high - isolated area with deteriorating weather"
    if weather_bad:
        return "moderate - weather expected to worsen"
    if access_bad:
        return "moderate - access route unavailable"
    return "none"