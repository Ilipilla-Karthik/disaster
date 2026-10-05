"""Geocoding tool (Agent 4).

Resolves free-text place names ("Zone A", "Zone B Community Hall") to
coordinates using **Nominatim** (OpenStreetMap). Falls back to a documented
local gazetteer so map features still render in offline demos.

Local fallback coordinates are district-level approximations and are always
tagged ``source="local_gazetteer"`` / ``verified=False``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["GeocodingTool", "geocode", "geocoding_status", "LOCAL_GAZETTEER"]


# Example District simulation gazetteer (synthetic).
LOCAL_GAZETTEER: dict[str, tuple[float, float]] = {
    "zone a": (12.9745, 77.5935),
    "zone b": (12.9610, 77.6080),
    "zone c": (12.9510, 77.6040),
    "zone b community hall": (12.9620, 77.6070),
    "zone c school grounds": (12.9500, 77.6050),
    "zone a sports complex": (12.9760, 77.5900),
    "response center 1": (12.9716, 77.5946),
    "response center 2": (12.9800, 77.6000),
    "zone b staging": (12.9600, 77.6100),
    "example district": (12.9716, 77.5946),
}


class GeocodingTool:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        user_agent: str = "disaster-response-system/1.0",
    ) -> None:
        self.base_url = (base_url or settings.GEOCODING_API_BASE).rstrip("/")
        self.timeout = timeout or settings.GEOCODING_API_TIMEOUT
        self.user_agent = user_agent
        self._live_available: bool | None = None

    async def geocode(self, query: str | None) -> dict[str, Any]:
        """Resolve a place name. Never invents a coordinate."""
        if not query:
            return self._miss("empty_query", "No location supplied to geocode.")

        key = str(query).strip().lower()

        # The bundled gazetteer is checked FIRST and exclusively for the
        # simulation dataset's own place names. A generic label such as
        # "Zone A" has no unique real-world referent, so a live geocoder would
        # happily return an unrelated municipality somewhere on the planet -
        # far worse than an explicitly unverified local coordinate.
        if key in LOCAL_GAZETTEER:
            lat, lon = LOCAL_GAZETTEER[key]
            return {
                "query": query,
                "latitude": lat,
                "longitude": lon,
                "display_name": f"{query} (simulation gazetteer)",
                "source": "local_gazetteer",
                "verified": False,
                "note": (
                    "Resolved from the bundled simulation gazetteer. "
                    "Approximate district-level coordinate - field verification "
                    "required. Not a surveyed position."
                ),
            }

        if self._live_available is not False:
            result = await self._fetch_nominatim(query)
            if result is not None:
                self._live_available = True
                return result
            self._live_available = False

        # Deliberately returns no coordinates instead of guessing.
        return self._miss(
            "not_resolved",
            f"Could not resolve '{query}' to coordinates. Geocoding verification "
            "required - the incident will be shown without a map position.",
        )

    async def _fetch_nominatim(self, query: str) -> dict[str, Any] | None:
        params = {"q": query, "format": "json", "limit": 1}
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.base_url}/search", params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("Nominatim unavailable: %s", exc)
            return None

        if not data:
            return None
        hit = data[0]
        return {
            "query": query,
            "latitude": float(hit.get("lat")),
            "longitude": float(hit.get("lon")),
            "display_name": hit.get("display_name"),
            "source": "nominatim",
            "verified": True,
            "note": "Live geocode from Nominatim (OpenStreetMap).",
        }

    def _miss(self, reason: str, note: str) -> dict[str, Any]:
        return {
            "query": None,
            "latitude": None,
            "longitude": None,
            "source": "unresolved",
            "verified": False,
            "reason": reason,
            "note": note,
        }


_tool = GeocodingTool()


async def geocode(query: str | None) -> dict[str, Any]:
    return await _tool.geocode(query)


def geocoding_status() -> dict[str, Any]:
    return {
        "configured": True,
        "mode": "live" if _tool._live_available is not False else "local_gazetteer",
        "provider": "Nominatim (OpenStreetMap)",
        "gazetteer_entries": len(LOCAL_GAZETTEER),
    }