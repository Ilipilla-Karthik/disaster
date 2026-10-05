"""Shared schema primitives."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import DISCLAIMER


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    message: str
    disclaimer: str = DISCLAIMER


class ErrorResponse(BaseModel):
    detail: str
    error_type: str | None = None
    verification_required: bool = False
    disclaimer: str = DISCLAIMER


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    simulation_mode: dict[str, bool]
    disclaimer: str = DISCLAIMER


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class PageMeta(BaseModel):
    page: Page


class TimestampMixinSchema(BaseModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AckRequest(BaseModel):
    """Generic acknowledgement payload."""

    note: str | None = Field(default=None, max_length=1000)


class IdList(BaseModel):
    ids: list[str]


class DictPayload(BaseModel):
    payload: dict[str, Any]