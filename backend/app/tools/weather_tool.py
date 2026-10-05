"""Weather & environmental intelligence tool (Requirement / Agent 3).

Integration strategy
--------------------
Primary source: **OpenWeatherMap** (configured via ``WEATHER_API_KEY``).
When no key is configured, or the API is unreachable, the tool falls back to a
**documented simulation model** and sets ``simulation_mode=True`` on the
response so no downstream agent can mistake simulated data for an observation.

The simulation is a deterministic function of (lat, lon, hour-of-day) plus an
optional scenario override, so demos are reproducible rather than random.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings

__all__ = ["WeatherTool", "get_weather", "weather_status"]


class WeatherTool:
    """Fetches or simulates current conditions and a 6-hour outlook."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.WEATHER_API_KEY
        self.base_url = (base_url or settings.WEATHER_API_BASE).rstrip("/")
        self.timeout = timeout or settings.WEATHER_API_TIMEOUT

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    # -- public API ---------------------------------------------------------

    async def get(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        """Return a normalised weather payload for a location.

        ``scenario`` (e.g. "severe_flood") forces the simulation model and is
        used by the test scenarios to reproduce the assignment brief.
        """
        if self.is_configured and latitude is not None and longitude is not None:
            result = await self._fetch_live(latitude, longitude)
            if result is not None:
                result["scenario"] = scenario
                return result
            return self._simulate(latitude, longitude, scenario, reason="live_api_failed")

        reason = "no_api_key" if not self.is_configured else "missing_coordinates"
        return self._simulate(latitude, longitude, scenario, reason=reason)

    # -- live provider ------------------------------------------------------

    async def _fetch_live(
        self, latitude: float, longitude: float
    ) -> dict[str, Any] | None:
        params = {
            "lat": latitude,
            "lon": longitude,
            "appid": self.api_key,
            "units": "metric",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                current = await client.get(f"{self.base_url}/weather", params=params)
                current.raise_for_status()
                cw = current.json()

                forecast = None
                try:
                    fresp = await client.get(f"{self.base_url}/forecast", params=params)
                    fresp.raise_for_status()
                    forecast = fresp.json()
                except Exception:  # forecast is best-effort
                    forecast = None
        except Exception:
            return None

        return _parse_openweather(cw, forecast)

    # -- documented simulation ---------------------------------------------

    def _simulate(
        self,
        latitude: float | None,
        longitude: float | None,
        scenario: str | None,
        reason: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        # Deterministic pseudo-weather from location + hour so repeated calls
        # in a demo are stable.
        seed = (
            abs((latitude or 12.9716) * 1000)
            + abs((longitude or 77.5946) * 1000)
            + now.hour
        )
        wave = math.sin(seed / 7.0)

        base_rain = 8.0 + (wave + 1.0) * 6.0  # 8-20 mm/h light-moderate

        severe = False
        worsening = False
        high_wind = False
        storm = False
        rainfall_6h = 20.0 + (wave + 1.0) * 15.0

        if scenario in {"severe_flood", "flood", "monsoon"}:
            base_rain = 42.0
            rainfall_6h = 80.0
            worsening = True
            high_wind = True
            storm = True
        elif scenario in {"deteriorating", "storm"}:
            base_rain = 28.0
            rainfall_6h = 55.0
            worsening = True
            high_wind = True
        elif scenario in {"clearing", "improving"}:
            base_rain = 2.0
            rainfall_6h = 5.0
            worsening = False

        # Severe thunderstorm warning threshold.
        severe = rainfall_6h >= 75 and (storm or high_wind)

        description = (
            "heavy rain" if base_rain >= 25 else "moderate rain" if base_rain >= 8 else "light rain"
        )

        return {
            "source": "simulation",
            "simulation_mode": True,
            "simulation_reason": reason,
            "observed_at": now.isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "condition": description,
            "description": (
                f"Simulated {description} conditions"
                + (" with a severe-weather warning in force." if severe else ".")
            ),
            "temp_c": round(26 + wave * 2, 1),
            "humidity_pct": round(min(99, 70 + base_rain * 0.7), 1),
            "wind_speed_ms": round(9 + (6 if high_wind else 0) + wave, 1),
            "wind_gust_ms": round(14 + (14 if high_wind else 0) + wave, 1),
            "rainfall_mm_last_hour": round(base_rain, 1),
            "rainfall_next_6h_mm": round(rainfall_6h, 1),
            "worsening": worsening,
            "severe_warning": severe,
            "storm": storm,
            "high_wind": high_wind,
            "visibility_km": round(1.0 if severe else 6.0, 1),
            "note": (
                "SIMULATED DATA - no live weather provider configured or reachable. "
                "Not an observation. Treat as indicative only."
            ),
        }


# ---------------------------------------------------------------------------
# Parsing / normalisation
# ---------------------------------------------------------------------------


def _parse_openweather(cw: dict[str, Any], forecast: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise OpenWeatherMap payloads into the internal weather schema."""
    main = cw.get("main") or {}
    wind = cw.get("wind") or {}
    rain = cw.get("rain") or {}
    snow = cw.get("snow") or {}
    sys = cw.get("sys") or {}
    weather_list = cw.get("weather") or [{}]

    rain_1h = float(rain.get("1h") or 0.0) + float(snow.get("1h") or 0.0)

    rainfall_6h = rain_1h * 6
    severe_ids = {200, 201, 202, 210, 211, 212, 221, 502, 503, 504, 711, 731, 761, 762, 771, 781}
    condition_id = int(weather_list[0].get("id") or 800)
    severe = condition_id in severe_ids

    temp_now = float(main.get("temp") or 0.0)
    temp_6h = temp_now
    if forecast:
        entries = forecast.get("list") or []
        later = entries[:6]
        if later:
            temps = [float((e.get("main") or {}).get("temp") or temp_now) for e in later]
            rainfall = sum(
                float((e.get("rain") or {}).get("3h") or 0.0) for e in later
            )
            rainfall_6h = round(rainfall, 1)
            temp_6h = temps[-1]
    worsening = temp_6h > temp_now + 1.0 or rainfall_6h >= 40.0

    return {
        "source": "openweathermap",
        "simulation_mode": False,
        "observed_at": datetime.fromtimestamp(
            cw.get("dt") or datetime.now(timezone.utc).timestamp(), tz=timezone.utc
        ).isoformat(),
        "latitude": cw.get("coord", {}).get("lat"),
        "longitude": cw.get("coord", {}).get("lon"),
        "condition": (weather_list[0].get("description") or "unknown").lower(),
        "description": (weather_list[0].get("description") or "").capitalize(),
        "temp_c": round(temp_now, 1),
        "feels_like_c": round(float(main.get("feels_like") or temp_now), 1),
        "humidity_pct": main.get("humidity"),
        "pressure_hpa": main.get("pressure"),
        "wind_speed_ms": round(float(wind.get("speed") or 0.0) * 3.6, 1),
        "wind_gust_ms": round(float(wind.get("gust") or 0.0) * 3.6, 1),
        "rainfall_mm_last_hour": round(rain_1h, 1),
        "rainfall_next_6h_mm": rainfall_6h,
        "worsening": worsening,
        "severe_warning": severe,
        "storm": severe and rain_1h > 0,
        "high_wind": float(wind.get("gust") or 0.0) * 3.6 >= 50,
        "visibility_km": (cw.get("visibility") or 10000) / 1000.0,
        "sunrise": _epoch(sys.get("sunrise")),
        "sunset": _epoch(sys.get("sunset")),
        "note": "Live observation from OpenWeatherMap.",
    }


def _epoch(value: Any) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------

_tool = WeatherTool()


async def get_weather(
    latitude: float | None = None,
    longitude: float | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    return await _tool.get(latitude, longitude, scenario)


def weather_status() -> dict[str, Any]:
    """Expose whether live weather is configured (for /health and the UI)."""
    return {
        "configured": _tool.is_configured,
        "mode": "live" if _tool.is_configured else "simulation",
        "provider": "OpenWeatherMap" if _tool.is_configured else "documented_simulation",
        "base_url": _tool.base_url,
    }