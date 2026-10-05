"""Backwards-compatible agent exports.

The agent implementations live in focused modules (``intake_agent``,
``assessment_agent``, ...). This module re-exports them under the flat names
that the rest of the codebase and the API layer import, so existing imports
such as ``from app.agents.base_agents import WeatherAgent`` keep working.
"""

from __future__ import annotations

from app.agents.allocation_agent import ResourceAllocationAgent
from app.agents.assessment_agent import SituationAssessmentAgent
from app.agents.base import AgentError, BaseAgent
from app.agents.coordination_agent import CoordinationAgent
from app.agents.geospatial_agent import GeospatialAgent
from app.agents.intake_agent import IncidentIntakeAgent
from app.agents.resource_agent import ResourceAgent
from app.agents.reviewer_agent import ReviewerAgent
from app.agents.weather_agent import WeatherAgent

__all__ = [
    "AgentError",
    "BaseAgent",
    "IncidentIntakeAgent",
    "SituationAssessmentAgent",
    "WeatherAgent",
    "GeospatialAgent",
    "ResourceAgent",
    "ResourceAllocationAgent",
    "CoordinationAgent",
    "ReviewerAgent",
]