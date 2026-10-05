"""Small shared helpers.

Kept dependency-free so both the ORM layer and the pure agent layer can use
them without creating an import cycle.
"""

from __future__ import annotations

from typing import Any

__all__ = ["enum_value", "field_get", "as_float", "as_int"]


def enum_value(value: Any) -> str:
    """Return the plain string for an enum member, string, or ``None``.

    Agents and services pass both ``IncidentType.FLOOD`` (ORM) and ``"flood"``
    (graph state) around. Normalising here means one code path handles both.
    """
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def field_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an object.

    The orchestration graph passes plain dicts; the API layer passes SQLAlchemy
    rows. Reading through one helper keeps behaviour identical for both.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None and default is not None else value


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default