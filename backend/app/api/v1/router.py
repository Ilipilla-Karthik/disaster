"""Aggregate API router for /api/v1."""

from fastapi import APIRouter

from app.api.v1 import alerts, chat, incidents, plans, reporting, resources, roads, shelters

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(incidents.router)
api_router.include_router(resources.router)
api_router.include_router(shelters.router)
api_router.include_router(roads.router)
api_router.include_router(plans.router)
api_router.include_router(alerts.router)
api_router.include_router(reporting.router)
api_router.include_router(chat.router)

__all__ = ["api_router"]