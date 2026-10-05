"""Application configuration.

All settings are environment-overridable. The system is designed to run fully
with zero external credentials by falling back to documented simulation modes
(see ``app.tools``), so a fresh clone can be demonstrated immediately.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # ---- Project metadata -------------------------------------------------
    PROJECT_NAME: str = "Disaster Response & Emergency Resource Coordination System"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = (
        "Agentic AI decision-support platform for emergency incident "
        "intake, severity assessment, resource allocation and coordination."
    )
    API_V1_STR: str = "/api/v1"

    # ---- Database ---------------------------------------------------------
    # PostgreSQL (production / Render). Set DATABASE_URL to override.
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/disaster_response"
    # SQLite fallback keeps local demos dependency-free (aiosqlite).
    SQLITE_URL: str = "sqlite+aiosqlite:///./disaster_response.db"
    DB_ECHO: bool = False
    # When PostgreSQL is unreachable at startup, rebind to SQLite so a fresh
    # clone is still runnable. Set False for strict production behaviour.
    DB_FALLBACK_TO_SQLITE: bool = True
    # Create tables + seed simulation data on startup when True.
    AUTO_CREATE_TABLES: bool = True
    AUTO_SEED: bool = True

    # ---- LLM --------------------------------------------------------------
    # provider: "openai" | "gemini" | "anthropic" | "none"
    LLM_PROVIDER: str = "none"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.0
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # ---- External integrations -------------------------------------------
    # Weather (OpenWeatherMap). Empty key -> simulation mode.
    WEATHER_API_KEY: str = ""
    WEATHER_API_BASE: str = "https://api.openweathermap.org/data/2.5"
    WEATHER_API_TIMEOUT: float = 6.0
    # Routing (OpenStreetMap OSRM public demo server). Unreachable -> simulation.
    ROUTING_API_BASE: str = "https://router.project-osrm.org"
    ROUTING_API_TIMEOUT: float = 6.0
    # Geocoding (Nominatim).
    GEOCODING_API_BASE: str = "https://nominatim.openstreetmap.org"
    GEOCODING_API_TIMEOUT: float = 6.0
    MAP_TILE_URL: str = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    MAPBOX_API_KEY: str = ""

    # ---- Domain thresholds ------------------------------------------------
    # Shelter utilisation % at which a capacity alert is raised.
    SHELTER_ALERT_THRESHOLD_PCT: float = 85.0
    # Temporal window used by duplicate detection (hours).
    DUPLICATE_WINDOW_HOURS: int = 6
    # Similarity floor (0-1) for description-based duplicate matching.
    DUPLICATE_SIMILARITY_THRESHOLD: float = 0.6
    # Geographic radius (km) considered "same place" for duplicate detection.
    DUPLICATE_RADIUS_KM: float = 2.0
    # A response action older than this (minutes) is flagged overdue.
    ACTION_OVERDUE_MINUTES: int = 120
    # Default planning horizon when OSRM is unavailable (minutes).
    FALLBACK_TRAVEL_MINUTES: int = 30

    # ---- Security / CORS --------------------------------------------------
    SECRET_KEY: str = "change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    # Simple role gate for human-in-the-loop endpoints.
    COORDINATOR_NAME: str = "duty.coordinator"
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


settings = Settings()