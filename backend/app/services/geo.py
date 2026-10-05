"""Shared geospatial helpers (pure functions, no I/O).

Uses the haversine formula on a spherical Earth. For district-scale emergency
planning (tens of km) the error versus a full geodesic solution is negligible
and the method avoids a mandatory GDAL/GEOS install.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def has_coords(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None


def valid_coords(lat: float | None, lon: float | None) -> bool:
    if not has_coords(lat, lon):
        return False
    return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180


def bbox_of(points: list[tuple[float, float]]) -> dict[str, float]:
    """Bounding box for a list of (lat, lon) points."""
    if not points:
        return {"min_lat": 0.0, "max_lat": 0.0, "min_lon": 0.0, "max_lon": 0.0}
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
    }


def centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def estimate_travel_minutes(distance_km: float, speed_kmh: float = 40.0) -> float:
    """Fallback travel-time estimate used when OSRM is unavailable.

    Deliberately labelled as an estimate by callers; never presented as a
    verified route time.
    """
    if distance_km <= 0:
        return 0.0
    return round((distance_km / max(speed_kmh, 1.0)) * 60.0, 1)