"""External tool integrations used by the agents.

Every tool exposes its own availability/simulation status so the UI can show
which figures are live observations and which are documented simulations.
"""

from app.tools.geocoding_tool import GeocodingTool, geocode, geocoding_status
from app.tools.llm_tool import LLMTool, get_llm, llm_status
from app.tools.pdf_tool import build_situation_report_pdf
from app.tools.routing_tool import (
    RoutingTool,
    assess_accessibility,
    get_route,
    routing_status,
)
from app.tools.weather_tool import WeatherTool, get_weather, weather_status

__all__ = [
    "GeocodingTool",
    "LLMTool",
    "RoutingTool",
    "WeatherTool",
    "assess_accessibility",
    "build_situation_report_pdf",
    "geocode",
    "geocoding_status",
    "get_llm",
    "get_route",
    "get_weather",
    "llm_status",
    "routing_status",
    "weather_status",
    "integration_status",
]


def integration_status() -> dict[str, dict]:
    """Aggregate integration status for /health and the dashboard."""
    return {
        "weather": weather_status(),
        "routing": routing_status(),
        "geocoding": geocoding_status(),
        "llm": llm_status(),
    }