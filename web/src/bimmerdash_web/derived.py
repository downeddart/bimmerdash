"""Trips, fill-ups and fuel prices: reads over the tables the collector derives."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from . import keys as K

PRICE_RANGE = (80.0, 260.0)  # pence/litre; ignore obvious typos in Fuel Finder data
STATION_PRICE_MAX_AGE_DAYS = 30


def _ready(conn: psycopg.Connection) -> bool:
    """The collector creates these tables; the web may start before it has."""
    return conn.execute("SELECT to_regclass('trips') IS NOT NULL").fetchone()[0]


def _rows(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# --- trips -------------------------------------------------------------------------


def _eco_cols(conn: psycopg.Connection) -> str:
    """ECO PRO share columns arrived in migration 008; older databases lack them."""
    has = conn.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'trips' AND column_name = 'eco_pro_pct'"
    ).fetchone()
    return (
        "eco_pro_pct, eco_pro_plus_pct" if has else "NULL AS eco_pro_pct, NULL AS eco_pro_plus_pct"
    )


def trips(conn: psycopg.Connection, vin: str, limit: int = 200) -> dict:
    if not _ready(conn):
        return {"summary": None, "trips": []}
    rows = _rows(
        conn.execute(
            "SELECT start_time, end_time, distance_km, start_odometer, end_odometer,"
            " start_lat, start_lon, end_lat, end_lon, fuel_start_l, fuel_end_l, fuel_used_l,"
            f" route, {_eco_cols(conn)} FROM trips"
            " WHERE vin = %s ORDER BY start_time DESC LIMIT %s",
            (vin, limit),
        )
    )
    from .usage import place_at, places

    named = places(conn)
    for r in rows:
        r["minutes"] = round((r["end_time"] - r["start_time"]).total_seconds() / 60)
        r["start"] = _point(r.pop("start_lat"), r.pop("start_lon"))
        r["end"] = _point(r.pop("end_lat"), r.pop("end_lon"))
        r["start_place"] = place_at(named, *(_coords(r["start"])))
        r["end_place"] = place_at(named, *(_coords(r["end"])))
        r["route"] = [{"time": t, "lat": la, "lon": lo} for t, la, lo in (r["route"] or [])]
        r["economy_l100"] = _l100(r["fuel_used_l"], r["distance_km"])
    return {"summary": trip_summary(conn, vin), "trips": rows}


def trip_summary(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    month = conn.execute(
        "SELECT count(*), COALESCE(sum(distance_km), 0),"
        " COALESCE(sum(extract(epoch FROM end_time - start_time)) / 60, 0)"
        " FROM trips WHERE vin = %s AND start_time >= date_trunc('month', %s::timestamptz)",
        (vin, now),
    ).fetchone()
    # Economy over recent trips that have fuel readings at both ends. Each reading is
    # whole litres, so only the total over many trips means much.
    econ = conn.execute(
        "SELECT sum(fuel_used_l), sum(distance_km), count(*) FROM trips"
        " WHERE vin = %s AND fuel_used_l IS NOT NULL"
        " AND start_time > %s::timestamptz - interval '90 days'",
        (vin, now),
    ).fetchone()
    return {
        "month_start": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        "month_trips": month[0],
        "month_km": round(month[1], 1),
        "month_minutes": round(month[2]),
        "economy_l100": _l100(econ[0], econ[1]) if econ[2] and econ[2] >= 5 else None,
        "economy_trips": econ[2],
    }


# --- fill-ups ----------------------------------------------------------------------


def with_intervals(fills: list[dict]) -> list[dict]:
    """Add distance and economy since the previous fill-up (fills in time order).

    Fuel burned between two fill-ups is what was in the tank after the first minus what
    was left before the second, so this works for partial fills too.
    """
    prev = None
    for f in fills:
        f["km_since_last"] = f["used_since_last_l"] = f["economy_l100"] = None
        if prev is not None and f["odometer"] is not None and prev["odometer"] is not None:
            km = f["odometer"] - prev["odometer"]
            used = (
                prev["litres_after"] - f["litres_before"]
                if prev["litres_after"] is not None and f["litres_before"] is not None
                else None
            )
            f["km_since_last"] = km if km > 0 else None
            if km > 0 and used is not None and used > 0:
                f["used_since_last_l"] = round(used, 1)
                f["economy_l100"] = _l100(used, km)
        prev = f
    return fills


def fuel(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    grade = _grade(conn, vin)
    if not _ready(conn):
        return {"grade": grade, "summary": None, "fillups": [], "monthly": []}
    fills = _rows(
        conn.execute(
            "SELECT time, odometer, litres_before, litres_after, percent_before, percent_after,"
            " litres_added, litres_estimated, lat, lon, node_id, station, distance_m,"
            " fuel_grade, fuel_kind, price_ppl, price_source, price_ref, cost_gbp"
            " FROM fillups WHERE vin = %s ORDER BY time",
            (vin,),
        )
    )
    with_intervals(fills)
    for f in fills:
        if f["station"] and f["station"].get("trading_name"):
            f["station"]["trading_name"] = tidy_name(f["station"]["trading_name"])
    intervals = [f for f in fills if f["economy_l100"] is not None][-10:]
    costed = [f for f in fills if f["cost_gbp"] is not None]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = [f for f in costed if f["time"] >= month_start]
    total = sum(f["cost_gbp"] for f in costed)
    estimated = sum(f["cost_gbp"] for f in costed if f["price_source"] == "uk_average")
    economy = (
        _l100(
            sum(f["used_since_last_l"] for f in intervals),
            sum(f["km_since_last"] for f in intervals),
        )
        if intervals
        else None
    )
    # Cost per km: recent economy at the recent average price paid.
    recent = [f for f in costed if f["litres_added"]][-5:]
    avg_ppl = (
        sum(f["cost_gbp"] for f in recent) * 100 / sum(f["litres_added"] for f in recent)
        if recent
        else None
    )
    summary = {
        "economy_l100": economy,
        "economy_intervals": len(intervals),
        "month_start": month_start,
        "month_cost": round(sum(f["cost_gbp"] for f in this_month), 2),
        "month_fillups": len(this_month),
        "total_cost": round(total, 2),
        "total_estimated": round(estimated, 2),
        "since": fills[0]["time"] if fills else None,
        "pence_per_km": round(economy / 100 * avg_ppl, 1) if economy and avg_ppl else None,
        "avg_price_ppl": round(avg_ppl, 1) if avg_ppl else None,
        "uncosted_fillups": len(fills) - len(costed),
    }
    from . import economy
    from .usage import local_price

    if summary["pence_per_km"] is None:
        # No priced fill-ups yet: the car's own economy at today's local price.
        best = economy.best(conn, vin, summary["economy_l100"])
        price = local_price(conn, vin)
        if best and price:
            summary["pence_per_km"] = round(best["l100"] / 100 * price["ppl"], 1)
            summary["avg_price_ppl"] = price["ppl"]
            summary["price_basis"] = price["source"]
    return {
        "grade": grade,
        "summary": summary,
        "fillups": list(reversed(fills)),
        "monthly": monthly(costed),
        # The car's own fuel counters: the most precise economy figure available.
        "car_economy": economy.car_economy(conn, vin, now),
    }


def monthly(costed: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for f in costed:
        m = out.setdefault(
            f["time"].strftime("%Y-%m"),
            {"month": f["time"].strftime("%Y-%m"), "cost": 0.0, "estimated": 0.0, "fillups": 0},
        )
        m["cost"] += f["cost_gbp"]
        m["fillups"] += 1
        if f["price_source"] == "uk_average":
            m["estimated"] += f["cost_gbp"]
    return [
        {**m, "cost": round(m["cost"], 2), "estimated": round(m["estimated"], 2)}
        for m in sorted(out.values(), key=lambda m: m["month"])
    ][-12:]


# --- nearby prices -----------------------------------------------------------------


def home(conn: psycopg.Connection, vin: str) -> dict | None:
    """Where the car usually parks: the most common trip end (to ~100 m), else its last fix."""
    if _ready(conn):
        row = conn.execute(
            "SELECT avg(end_lat), avg(end_lon), count(*) FROM trips"
            " WHERE vin = %s AND end_lat IS NOT NULL"
            " GROUP BY round(end_lat::numeric, 3), round(end_lon::numeric, 3)"
            " ORDER BY count(*) DESC LIMIT 1",
            (vin,),
        ).fetchone()
        if row and row[2] >= 2:
            return {"lat": row[0], "lon": row[1], "source": "home"}
    row = conn.execute(
        "SELECT max(value_num) FILTER (WHERE key = ANY(%s)),"
        " max(value_num) FILTER (WHERE key = ANY(%s))"
        " FROM latest_values WHERE vin = %s",
        (K.LATITUDE, K.LONGITUDE, vin),
    ).fetchone()
    if row and row[0] is not None and row[1] is not None:
        return {"lat": row[0], "lon": row[1], "source": "car"}
    return None


def nearby_prices(conn: psycopg.Connection, vin: str, radius_km: float = 5, limit: int = 8) -> dict:
    centre = home(conn, vin)
    grade = _grade(conn, vin)
    if centre is None:
        return {"centre": None, "grade": grade, "stations": []}
    rows = stations_near(conn, centre["lat"], centre["lon"], grade, radius_km, limit)
    return {"centre": centre, "grade": grade, "radius_km": radius_km, "stations": rows}


def stations_near(
    conn: psycopg.Connection,
    lat: float,
    lon: float,
    grade: str,
    radius_km: float,
    limit: int = 8,
    order: str = "price",
) -> list[dict]:
    """Open forecourts within radius_km with a recent, plausible price for this grade."""
    rows = _rows(
        conn.execute(
            f"""
            WITH near AS (
                SELECT node_id, trading_name, brand_name, postcode, latitude, longitude,
                       6371 * sqrt(power(radians(latitude - %(lat)s), 2)
                         + power(radians(longitude - %(lon)s) * cos(radians(%(lat)s)), 2)) AS km
                FROM forecourts
                WHERE NOT closed
                  AND latitude BETWEEN %(lat)s - %(dlat)s AND %(lat)s + %(dlat)s
                  AND longitude BETWEEN %(lon)s - %(dlon)s AND %(lon)s + %(dlon)s
            )
            SELECT n.node_id, n.trading_name AS name, n.brand_name AS brand, n.postcode,
                   n.latitude AS lat, n.longitude AS lon, round(n.km::numeric, 1) AS distance_km,
                   p.price, p.effective_at AS updated_at
            FROM near n
            CROSS JOIN LATERAL (
                SELECT price, effective_at FROM fuel_prices
                WHERE node_id = n.node_id AND fuel_type = %(grade)s
                ORDER BY effective_at DESC LIMIT 1
            ) p
            WHERE n.km <= %(radius)s AND p.price BETWEEN %(pmin)s AND %(pmax)s
              AND p.effective_at > now() - make_interval(days => %(max_age)s)
            ORDER BY {"n.km" if order == "distance" else "p.price, n.km"} LIMIT %(limit)s
            """,
            {
                "lat": lat,
                "lon": lon,
                "dlat": radius_km / 111.32,
                "dlon": radius_km / 111.32 / max(0.2, abs(_cos(lat))),
                "grade": grade,
                "radius": radius_km,
                "pmin": PRICE_RANGE[0],
                "pmax": PRICE_RANGE[1],
                "max_age": STATION_PRICE_MAX_AGE_DAYS,
                "limit": limit,
            },
        )
    )
    for r in rows:
        r["distance_km"] = float(r["distance_km"])
        r["name"] = tidy_name(r["name"])
    return rows


# --- helpers -----------------------------------------------------------------------


def _grade(conn: psycopg.Connection, vin: str) -> str:
    if conn.execute("SELECT to_regclass('settings') IS NOT NULL").fetchone()[0]:
        row = conn.execute(
            "SELECT value #>> '{}' FROM settings WHERE scope = %s AND key = 'fuel_grade'", (vin,)
        ).fetchone()
        if row and row[0]:
            return row[0]
    has_col = conn.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = 'vehicles' AND column_name = 'fuel_grade'"
    ).fetchone()
    if has_col:
        row = conn.execute("SELECT fuel_grade FROM vehicles WHERE vin = %s", (vin,)).fetchone()
        if row and row[0]:
            return row[0]
    return "E10"


def tidy_name(name: str | None) -> str | None:
    """Fuel Finder names are often upper case with stray punctuation."""
    if not name:
        return name
    name = name.strip().rstrip(",.;-").strip()
    if name.isupper():
        small = {"and", "of", "the", "at", "on"}
        words = name.lower().split()
        name = " ".join(
            w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words)
        )
    return name


def _coords(p: dict | None) -> tuple:
    return (p["lat"], p["lon"]) if p else (None, None)


def _point(lat, lon) -> dict | None:
    return None if lat is None or lon is None else {"lat": lat, "lon": lon}


def _l100(litres, km) -> float | None:
    if not litres or not km or km <= 0:
        return None
    return round(100 * litres / km, 1)


def _cos(deg: float) -> float:
    import math

    return math.cos(math.radians(deg))
