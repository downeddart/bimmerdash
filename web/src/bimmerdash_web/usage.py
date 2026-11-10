"""Mileage and usage: annual projection, monthly distance, when and where you drive."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import psycopg
from pydantic import BaseModel, Field

from . import keys as K

CLUSTER_M = 200  # trip ends this close together count as one destination
MIN_RECENT_DAYS = 28  # odometer history needed before the recent rate is trusted
SHORT_TRIP_KM = 5


class NewPlace(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(150, ge=25, le=2000)


class PlaceName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# --- places ------------------------------------------------------------------------


def _places_ready(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('places') IS NOT NULL").fetchone()[0]


def places(conn: psycopg.Connection) -> list[dict]:
    if not _places_ready(conn):
        return []
    rows = conn.execute("SELECT id, name, lat, lon, radius_m FROM places ORDER BY name").fetchall()
    return [dict(zip(("id", "name", "lat", "lon", "radius_m"), r, strict=True)) for r in rows]


def place_at(named: list[dict], lat: float | None, lon: float | None) -> dict | None:
    """The closest named place whose radius covers this point."""
    if lat is None or lon is None:
        return None
    best = None
    for p in named:
        d = haversine_m(lat, lon, p["lat"], p["lon"])
        if d <= p["radius_m"] and (best is None or d < best[0]):
            best = (d, p)
    return None if best is None else {"id": best[1]["id"], "name": best[1]["name"]}


def add_place(conn: psycopg.Connection, p: NewPlace) -> dict:
    row = conn.execute(
        "INSERT INTO places (name, lat, lon, radius_m) VALUES (%s, %s, %s, %s) RETURNING id",
        (p.name.strip(), p.lat, p.lon, p.radius_m),
    ).fetchone()
    return {"id": row[0], **p.model_dump(), "name": p.name.strip()}


def rename_place(conn: psycopg.Connection, place_id: int, name: str) -> bool:
    return (
        conn.execute("UPDATE places SET name = %s WHERE id = %s", (name.strip(), place_id)).rowcount
        == 1
    )


def delete_place(conn: psycopg.Connection, place_id: int) -> bool:
    return conn.execute("DELETE FROM places WHERE id = %s", (place_id,)).rowcount == 1


def destinations(ends: list[dict], named: list[dict], limit: int = 8) -> list[dict]:
    """Group trip end points: first by named place, then into ~200 m clusters."""
    groups: list[dict] = []
    by_place: dict[int, dict] = {}
    for e in ends:
        if e["lat"] is None:
            continue
        hit = place_at(named, e["lat"], e["lon"])
        if hit is not None:
            g = by_place.get(hit["id"])
            if g is None:
                p = next(x for x in named if x["id"] == hit["id"])
                g = by_place[hit["id"]] = {
                    "place_id": p["id"],
                    "name": p["name"],
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "visits": 0,
                    "last_visit": None,
                    "_pts": [],
                }
                groups.append(g)
        else:
            g = next(
                (
                    c
                    for c in groups
                    if c["place_id"] is None
                    and haversine_m(e["lat"], e["lon"], c["lat"], c["lon"]) <= CLUSTER_M
                ),
                None,
            )
            if g is None:
                g = {
                    "place_id": None,
                    "name": None,
                    "lat": e["lat"],
                    "lon": e["lon"],
                    "visits": 0,
                    "last_visit": None,
                    "_pts": [],
                }
                groups.append(g)
        g["visits"] += 1
        g["_pts"].append((e["lat"], e["lon"]))
        if g["last_visit"] is None or e["time"] > g["last_visit"]:
            g["last_visit"] = e["time"]
        if g["place_id"] is None:  # unnamed: keep the centre on the average
            g["lat"] = sum(p[0] for p in g["_pts"]) / len(g["_pts"])
            g["lon"] = sum(p[1] for p in g["_pts"]) / len(g["_pts"])
    for g in groups:
        del g["_pts"]
    groups.sort(key=lambda g: (-g["visits"], g["place_id"] is None, -g["last_visit"].timestamp()))
    return groups[:limit]


# --- mileage -----------------------------------------------------------------------


def _built(conn: psycopg.Connection, vin: str) -> datetime | None:
    row = conn.execute(
        "SELECT basic_data->>'firstRegistrationDate', basic_data->>'constructionDate'"
        " FROM vehicles WHERE vin = %s",
        (vin,),
    ).fetchone()
    for raw in row or ():
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("+0000", "+00:00").replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def mileage(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    odo = conn.execute(
        "SELECT min(time), max(time),"
        " (SELECT value_num FROM telemetry WHERE vin = %(v)s AND key = ANY(%(k)s)"
        "  AND value_num IS NOT NULL ORDER BY time DESC LIMIT 1)"
        " FROM telemetry WHERE vin = %(v)s AND key = ANY(%(k)s) AND value_num IS NOT NULL",
        {"v": vin, "k": K.ODOMETER},
    ).fetchone()
    first_at, last_at, odometer = odo
    built = _built(conn, vin)

    lifetime = None
    if odometer and built:
        years = (now - built).days / 365.25
        if years > 0.25:
            lifetime = round(odometer / years)

    # Recent rate: distance over the last year (or all the history we have, if less).
    recent = None
    days = (last_at - first_at).total_seconds() / 86400 if first_at and last_at else 0
    if days >= MIN_RECENT_DAYS:
        row = conn.execute(
            "SELECT min(value_num), max(value_num), min(time), max(time) FROM telemetry"
            " WHERE vin = %s AND key = ANY(%s) AND value_num IS NOT NULL"
            " AND time > %s::timestamptz - interval '365 days'",
            (vin, K.ODOMETER, now),
        ).fetchone()
        span = (row[3] - row[2]).total_seconds() / 86400
        if span >= MIN_RECENT_DAYS:
            recent = round((row[1] - row[0]) / span * 365.25)

    months = conn.execute(
        """
        WITH m AS (
            SELECT date_trunc('month', time) AS month, min(value_num) AS lo, max(value_num) AS hi
            FROM telemetry
            WHERE vin = %s AND key = ANY(%s) AND value_num IS NOT NULL
              AND time > %s::timestamptz - interval '13 months'
            GROUP BY 1
        )
        SELECT month, hi - COALESCE(lag(hi) OVER (ORDER BY month), lo) FROM m ORDER BY month
        """,
        (vin, K.ODOMETER, now),
    ).fetchall()

    return {
        "odometer_km": odometer,
        "built": built,
        "history_from": first_at,
        "history_days": round(days, 1),
        "lifetime_km_per_year": lifetime,
        "recent_km_per_year": recent,
        "estimate_km_per_year": recent or lifetime,
        "estimate_basis": "recent" if recent else "lifetime" if lifetime else None,
        "monthly": [{"month": m.strftime("%Y-%m"), "km": round(km, 1)} for m, km in months][-12:],
    }


# --- everything for the Insights page ----------------------------------------------


def usage(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    named = places(conn)
    out = {"mileage": mileage(conn, vin, now), "places": named, "trips": [], "destinations": []}
    if not conn.execute("SELECT to_regclass('trips') IS NOT NULL").fetchone()[0]:
        out["stats"] = None
        return out
    rows = conn.execute(
        "SELECT start_time, end_time, distance_km, end_lat, end_lon FROM trips"
        " WHERE vin = %s ORDER BY start_time",
        (vin,),
    ).fetchall()
    # Light rows for the heatmap; the browser buckets them in its own time zone.
    out["trips"] = [
        {"start": s, "minutes": round((e - s).total_seconds() / 60), "km": km}
        for s, e, km, _, _ in rows
    ]
    out["destinations"] = destinations(
        [{"time": e, "lat": la, "lon": lo} for _, e, _, la, lo in rows], named
    )
    km = [r[2] for r in rows]
    span_days = (rows[-1][0] - rows[0][0]).total_seconds() / 86400 if len(rows) > 1 else 0
    out["stats"] = {
        "trips": len(rows),
        "km": round(sum(km), 1),
        "avg_trip_km": round(sum(km) / len(km), 1) if km else None,
        "longest_trip_km": max(km) if km else None,
        "short_trip_share": round(sum(1 for k in km if k < SHORT_TRIP_KM) / len(km), 2)
        if km
        else None,
        "trips_per_week": round(len(rows) / span_days * 7, 1) if span_days >= 7 else None,
    }
    return out
