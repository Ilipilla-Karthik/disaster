"""Agent 3 - Weather & Environmental Intelligence Agent.

Retrieves current conditions and a short-range outlook, then translates them
into the specific operational questions other agents care about:

* Does this weather worsen the incident?
* Does it constrain the access mode required?
* Is the figure an observation or a simulation?

The last point matters: the agent propagates ``simulation_mode`` so that
downstream scoring can still be computed, but the operational picture is always
labelled so no coordinator mistakes simulated weather for a real forecast.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.orchestration.state import WorkflowState
from app.services.allocation_engine import estimate_requirement
from app.tools import get_weather

logger = logging.getLogger(__name__)

__all__ = ["WeatherAgent"]


class WeatherAgent(BaseAgent):
    name = "weather_environmental_intelligence"
    role = "Retrieve and interpret weather and environmental conditions"
    node_name = "weather_output"

    system_prompt = """
You summarise weather conditions for emergency operations. You never invent
weather data - you only interpret the supplied observation or simulation.
Always state whether the data is simulated.
""".strip()

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        incidents = state.get("normalized_incidents") or state.get("incidents") or []
        scenario = state.get("scenario")

        per_incident: dict[str, Any] = {}
        notes: list[str] = []

        for incident in incidents:
            iid = str(incident.get("incident_id"))
            weather = await get_weather(
                incident.get("latitude"), incident.get("longitude"), scenario=scenario
            )
            assessment = self._interpret(incident, weather)
            per_incident[iid] = {**weather, **assessment}

            if weather.get("simulation_mode"):
                notes.append(
                    f"Weather for {iid} is SIMULATED ({weather.get('simulation_reason')}); "
                    "not an observation."
                )

        # A single district-level snapshot is enough for the situation report.
        snapshot = None
        if per_incident:
            first = next(iter(per_incident.values()))
            snapshot = {
                k: first.get(k)
                for k in (
                    "source", "simulation_mode", "condition", "temp_c",
                    "rainfall_mm_last_hour", "rainfall_next_6h_mm", "worsening",
                    "severe_warning", "wind_speed_ms", "visibility_km", "note",
                )
            }

        warnings = [
            iid for iid, w in per_incident.items() if w.get("severe_warning")
        ]
        worsening = [iid for iid, w in per_incident.items() if w.get("worsening")]

        return {
            "weather_output": {
                "per_incident": per_incident,
                "snapshot": snapshot,
                "incidents_with_severe_warning": warnings,
                "incidents_worsening": worsening,
                "simulation_mode": bool(
                    snapshot and snapshot.get("simulation_mode")
                ),
                "notes": notes,
            }
        }

    @staticmethod
    def _interpret(incident: dict[str, Any], weather: dict[str, Any]) -> dict[str, Any]:
        """Translate raw conditions into operational implications."""
        requirement = estimate_requirement(incident)
        worsening = bool(weather.get("worsening"))
        severe = bool(weather.get("severe_warning"))
        rainfall = float(weather.get("rainfall_next_6h_mm") or 0.0)
        wind = float(weather.get("wind_speed_ms") or 0.0)

        affects = worsening or severe or rainfall >= 40
        drivers: list[str] = []
        if severe:
            drivers.append("severe-weather warning in force")
        if worsening:
            drivers.append("conditions expected to worsen")
        if rainfall >= 40:
            drivers.append(f"{rainfall}mm rain forecast in next 6h")
        if wind >= 15:
            drivers.append(f"wind {wind} m/s may affect boats and light vehicles")

        # Water-incident + heavy rain => flood extent likely to grow, so the
        # population figure is probably a floor, not a ceiling.
        likely_underestimate = bool(
            requirement.is_water_incident and rainfall >= 40
        )

        constraints: list[str] = []
        if severe:
            constraints.append(
                "Severe-weather warning in force - deployment subject to the "
                "commander's weather go/no-go decision."
            )
        if requirement.is_water_incident and rainfall >= 40:
            constraints.append(
                "Water-rescue operations during heavy rain: transit time and "
                "boat survivability must be confirmed by the operator."
            )

        return {
            "affects_operations": affects,
            "worsening": worsening,
            "severe_warning": severe,
            "drivers": drivers,
            "constraints": constraints,
            "likely_underestimates_population": likely_underestimate,
            "verification_required": bool(weather.get("simulation_mode")),
        }