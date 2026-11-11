"""Mileage and usage: annual projection, monthly distance, when and where you drive."""

from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime

import psycopg
from pydantic import BaseModel, Field

from . import economy
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

    estimate = recent or lifetime
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
        "limit": mileage_limit(conn, vin, odometer, estimate, now),
    }


def _anniversary(start: date, today: date) -> date:
    """The latest anniversary of `start` on or before today."""

    def on(year: int) -> date:
        try:
            return start.replace(year=year)
        except ValueError:  # 29 Feb
            return date(year, 2, 28)

    a = on(today.year)
    return a if a <= today else on(today.year - 1)


def mileage_limit(
    conn: psycopg.Connection,
    vin: str,
    odometer: float | None,
    km_per_year: float | None,
    now: datetime,
) -> dict | None:
    """Progress against a yearly mileage limit (insurance, PCP or lease), if one is set."""
    if not conn.execute("SELECT to_regclass('settings') IS NOT NULL").fetchone()[0]:
        return None
    rows = dict(
        conn.execute(
            "SELECT key, value FROM settings WHERE scope = %s"
            " AND key IN ('mileage_limit_km', 'mileage_year_start')",
            (vin,),
        ).fetchall()
    )
    limit = rows.get("mileage_limit_km")
    if not limit or odometer is None:
        return None
    today = now.date()
    try:
        start = _anniversary(date.fromisoformat(rows.get("mileage_year_start") or ""), today)
    except ValueError:
        start = date(today.year, 1, 1)
    try:
        end = start.replace(year=start.year + 1)
    except ValueError:
        end = date(start.year + 1, 2, 28)
    start_at = datetime(start.year, start.month, start.day, tzinfo=UTC)
    row = conn.execute(
        "SELECT value_num, time FROM telemetry WHERE vin = %s AND key = ANY(%s)"
        " AND value_num IS NOT NULL AND time >= %s ORDER BY time LIMIT 1",
        (vin, K.ODOMETER, start_at),
    ).fetchone()
    per_day = (km_per_year or 0) / 365.25
    estimated = False
    if row is None:
        return None
    odo_start, first_at = row
    gap_days = (first_at - start_at).total_seconds() / 86400
    if gap_days > 1:
        # History starts after the limit year began: assume the usual pace before it.
        odo_start -= per_day * gap_days
        estimated = True
    used = max(0.0, odometer - odo_start)
    days_left = (end - today).days
    projected = used + per_day * days_left
    return {
        "limit_km": float(limit),
        "year_start": start,
        "year_end": end,
        "used_km": round(used),
        "used_estimated": estimated,
        "projected_km": round(projected) if km_per_year else None,
        "remaining_km": round(float(limit) - used),
        "days_left": days_left,
        "on_track": projected <= float(limit) if km_per_year else None,
    }


# --- everything for the Insights page ----------------------------------------------


def usage(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    named = places(conn)
    out = {
        "mileage": mileage(conn, vin, now),
        "places": named,
        "trips": [],
        "destinations": [],
        "routes": [],
        "costs": None,
    }
    if not conn.execute("SELECT to_regclass('trips') IS NOT NULL").fetchone()[0]:
        out["stats"] = None
        return out
    rows = conn.execute(
        "SELECT start_time, end_time, distance_km, end_lat, end_lon, start_lat, start_lon"
        " FROM trips"
        " WHERE vin = %s ORDER BY start_time",
        (vin,),
    ).fetchall()
    # Light rows for the heatmap; the browser buckets them in its own time zone.
    out["trips"] = [
        {"start": s, "minutes": round((e - s).total_seconds() / 60), "km": km}
        for s, e, km, *_ in rows
    ]
    out["destinations"] = destinations(
        [{"time": e, "lat": la, "lon": lo} for _, e, _, la, lo, *_ in rows], named
    )
    costs = cost_projection(conn, vin, out["mileage"]["estimate_km_per_year"])
    out["costs"] = costs
    out["routes"] = routes(
        [
            {
                "start": (sla, slo),
                "end": (la, lo),
                "km": km,
                "minutes": (e - st).total_seconds() / 60,
            }
            for st, e, km, la, lo, sla, slo in rows
        ],
        named,
        costs,
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


# --- costs and routes --------------------------------------------------------------


def local_price(conn: psycopg.Connection, vin: str) -> dict | None:
    """Typical pump price where you drive: median nearby, else the UK weekly average."""
    from .derived import _grade, home, stations_near

    grade = _grade(conn, vin)
    centre = home(conn, vin)
    if centre is not None:
        near = stations_near(conn, centre["lat"], centre["lon"], grade, 8, limit=30)
        if len(near) >= 3:
            return {
                "ppl": round(statistics.median(s["price"] for s in near), 1),
                "source": "local",
                "grade": grade,
            }
    kind = "diesel" if grade.startswith("B") or grade == "HVO" else "petrol"
    row = conn.execute(
        "SELECT price FROM uk_average_fuel_prices WHERE fuel = %s ORDER BY week_start DESC LIMIT 1",
        (kind,),
    ).fetchone()
    return {"ppl": row[0], "source": "uk_average", "grade": grade} if row else None


def cost_projection(conn: psycopg.Connection, vin: str, km_per_year: float | None) -> dict | None:
    from .derived import fuel

    fill_l100 = (fuel(conn, vin).get("summary") or {}).get("economy_l100")
    econ = economy.best(conn, vin, fill_l100)
    price = local_price(conn, vin)
    if econ is None or price is None:
        return {
            "economy_l100": econ["l100"] if econ else None,
            "economy_source": econ["source"] if econ else None,
            "price_ppl": price["ppl"] if price else None,
            "price_source": price["source"] if price else None,
            "pence_per_km": None,
            "km_per_year": km_per_year,
            "cost_per_year": None,
            "cost_per_month": None,
            "litres_per_year": None,
        }
    ppk = econ["l100"] / 100 * price["ppl"]
    litres = km_per_year * econ["l100"] / 100 if km_per_year else None
    return {
        "economy_l100": econ["l100"],
        "economy_source": econ["source"],
        "price_ppl": price["ppl"],
        "price_source": price["source"],
        "pence_per_km": round(ppk, 2),
        "km_per_year": km_per_year,
        "litres_per_year": round(litres) if litres else None,
        "cost_per_year": round(km_per_year * ppk / 100) if km_per_year else None,
        "cost_per_month": round(km_per_year * ppk / 100 / 12) if km_per_year else None,
    }


def routes(trips: list[dict], named: list[dict], costs: dict | None, limit: int = 8) -> list[dict]:
    """Trips grouped by named start and end place ("Home -> Work"), busiest first."""
    groups: dict[tuple, dict] = {}
    ppk = (costs or {}).get("pence_per_km")
    for t in trips:
        a = place_at(named, *t["start"])
        b = place_at(named, *t["end"])
        if a is None and b is None:
            key = ("", "")
        else:
            key = (a["name"] if a else "Elsewhere", b["name"] if b else "Elsewhere")
        g = groups.setdefault(
            key,
            {
                "from": key[0] or None,
                "to": key[1] or None,
                "trips": 0,
                "km": 0.0,
                "minutes": 0.0,
                "short": 0,
            },
        )
        g["trips"] += 1
        g["km"] += t["km"]
        g["minutes"] += t["minutes"]
        g["short"] += 1 if t["km"] < SHORT_TRIP_KM else 0
    out = []
    for g in groups.values():
        out.append(
            {
                **g,
                "km": round(g["km"], 1),
                "avg_km": round(g["km"] / g["trips"], 1),
                "avg_minutes": round(g["minutes"] / g["trips"]),
                "minutes": round(g["minutes"]),
                "cost": round(g["km"] * ppk / 100, 2) if ppk else None,
            }
        )
    # "Other" (no named place at either end) goes last.
    out.sort(key=lambda g: (g["from"] is None, -g["trips"], -g["km"]))
    return out[:limit]
