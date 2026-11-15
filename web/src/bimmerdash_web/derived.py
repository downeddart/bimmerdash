"""Trips, fill-ups and fuel prices: reads over the tables the collector derives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from . import keys as K

PRICE_RANGE = (80.0, 260.0)  # pence/litre; ignore obvious typos in Fuel Finder data
STATION_PRICE_MAX_AGE_DAYS = 30
# The car reports fuel in whole litres, so a trip's measured fuel is only meaningful on a
# long drive, and the Trips page total only once enough measured fuel is behind it.
LONG_TRIP_KM = 50
MIN_TRIP_TOTAL_LITRES = 10


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
    from . import economy

    fills = fill_intervals(conn, vin)
    fill_l100 = fillup_economy(fills)
    plan = economy.best(conn, vin, fill_l100)
    trip_fuel(rows, fills, plan["l100"] if plan else None)
    price = fuel_price(conn, vin)
    for r in rows:
        r["cost_gbp"] = (
            round(r["fuel_l"] * price["ppl"] / 100, 2) if price and r["fuel_l"] else None
        )
    summary = trip_summary(conn, vin, fillup_l100=fill_l100, plan=plan)
    if summary["economy_l100"] and price:
        summary["month_cost_gbp"] = round(
            summary["month_km"] * summary["economy_l100"] / 100 * price["ppl"] / 100, 2
        )
    summary["price"] = price
    return {"summary": summary, "trips": rows}


def fuel_price(conn: psycopg.Connection, vin: str) -> dict | None:
    """Price per litre to cost driving with: what you paid lately, else local pumps."""
    fills = _rows(
        conn.execute(
            "SELECT time, cost_gbp, litres_added, litres_estimated, price_source, price_ppl"
            " FROM fillups WHERE vin = %s ORDER BY time DESC LIMIT 5",
            (vin,),
        )
    )
    from . import receipts

    receipts.apply(fills, receipts.load(conn, vin))
    paid = [f for f in fills if f["cost_gbp"] and f["litres_added"]]
    if paid:
        ppl = sum(f["cost_gbp"] for f in paid) * 100 / sum(f["litres_added"] for f in paid)
        estimated = all(f["price_source"] == "uk_average" for f in paid)
        return {"ppl": round(ppl, 1), "source": "uk_average" if estimated else "paid"}
    from .usage import local_price

    p = local_price(conn, vin)
    return {"ppl": p["ppl"], "source": p["source"]} if p else None


def trip_fuel(trips: list[dict], fills: list[dict], fallback_l100: float | None = None) -> None:
    """What each trip shows for fuel: fuel_l, fuel_basis and (long trips) economy_l100.

    A long trip shows its measured fuel and economy. A shorter one shows an estimate
    (marked as one) from the economy between the fill-ups either side of it, else the
    recent fill-up average: its whole-litre reading is mostly rounding. Without any
    fill-up economy it falls back to the measured figure.
    """
    spans = [
        (a["time"], b["time"], b["economy_l100"])
        for a, b in zip(fills, fills[1:], strict=False)
        if b["economy_l100"] is not None
    ]
    recent = fillup_economy(fills) or fallback_l100
    for t in trips:
        km = t["distance_km"] or 0
        measured = t["fuel_used_l"]
        long = km >= LONG_TRIP_KM
        t["economy_l100"] = _l100(measured, km) if long else None
        l100 = next((e for a, b, e in spans if a <= t["start_time"] < b), recent)
        t["fuel_estimated_l"] = round(km * l100 / 100, 1) if l100 and km > 0 else None
        if (long and measured is not None) or t["fuel_estimated_l"] is None:
            t["fuel_l"], t["fuel_basis"] = measured, "measured" if measured is not None else None
        else:
            t["fuel_l"], t["fuel_basis"] = t["fuel_estimated_l"], "estimate"


def trip_summary(
    conn: psycopg.Connection,
    vin: str,
    now: datetime | None = None,
    fillup_l100: float | None = None,
    plan: dict | None = None,
) -> dict:
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
    if econ[0] is not None and econ[0] >= MIN_TRIP_TOTAL_LITRES:
        l100, source = _l100(econ[0], econ[1]), "trips"
    elif fillup_l100:
        l100, source = fillup_l100, "fillups"
    elif plan:
        l100, source = plan["l100"], plan["source"]
    else:
        l100, source = None, None
    return {
        "month_start": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        "month_trips": month[0],
        "month_km": round(month[1], 1),
        "month_minutes": round(month[2]),
        "economy_l100": l100,
        "economy_source": source,
        "economy_trips": econ[2],
    }


# --- fill-ups ----------------------------------------------------------------------


def fill_intervals(conn: psycopg.Connection, vin: str) -> list[dict]:
    """Fill-ups in time order with the distance and economy since the one before."""
    return with_intervals(
        _rows(
            conn.execute(
                "SELECT time, odometer, litres_before, litres_after FROM fillups"
                " WHERE vin = %s ORDER BY time",
                (vin,),
            )
        )
    )


def fillup_economy(fills: list[dict]) -> float | None:
    """Economy over the last 10 fill-up intervals (fills as from with_intervals)."""
    intervals = [f for f in fills if f["economy_l100"] is not None][-10:]
    if not intervals:
        return None
    return _l100(
        sum(f["used_since_last_l"] for f in intervals),
        sum(f["km_since_last"] for f in intervals),
    )


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
    from . import receipts

    receipts.apply(fills, receipts.load(conn, vin))
    for f in fills:
        if f["station"] and f["station"].get("trading_name"):
            f["station"]["trading_name"] = tidy_name(f["station"]["trading_name"])
    from . import economy as _econ

    plan = _econ.best(conn, vin, fillup_economy(fills))
    missed_savings(conn, fills, plan["l100"] if plan else 7.0)
    intervals = [f for f in fills if f["economy_l100"] is not None][-10:]
    costed = [f for f in fills if f["cost_gbp"] is not None]
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = [f for f in costed if f["time"] >= month_start]
    total = sum(f["cost_gbp"] for f in costed)
    estimated = sum(f["cost_gbp"] for f in costed if f["price_source"] == "uk_average")
    economy = fillup_economy(fills)
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
        "missed_savings_gbp": round(sum(f["could_save_gbp"] or 0 for f in fills), 2),
        "fillups_compared": sum(1 for f in fills if f["could_save_gbp"] is not None),
    }
    from . import economy, gauge
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
        # Without counters: economy from the fuel gauge and the range readout.
        "gauge": gauge.gauge_economy(conn, vin, now),
        "price_trend": price_trend(conn, vin),
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


def price_trend(conn: psycopg.Connection, vin: str, days: int = 60, radius_km: float = 8) -> dict:
    """Pump prices near home over time: the middle and cheapest price each day.

    Each station's price on a day is the last one it reported by the end of that day.
    """
    centre = home(conn, vin)
    grade = _grade(conn, vin)
    out = {"grade": grade, "daily": [], "change_7d": None, "uk_avg": None, "vs_uk": None}
    if centre is None:
        return out
    near = stations_near(conn, centre["lat"], centre["lon"], grade, radius_km, limit=60)
    ids = [s["node_id"] for s in near]
    if len(ids) < 3:
        return out
    rows = conn.execute(
        """
        SELECT d::date, percentile_cont(0.5) WITHIN GROUP (ORDER BY p.price), min(p.price),
               count(*)
        FROM generate_series(current_date - %(days)s, current_date, interval '1 day') d
        CROSS JOIN unnest(%(ids)s::text[]) AS s(node_id)
        CROSS JOIN LATERAL (
            SELECT price FROM fuel_prices
            WHERE node_id = s.node_id AND fuel_type = %(grade)s
              AND effective_at < d + interval '1 day'
              AND effective_at > d - interval '30 days'
              AND price BETWEEN %(lo)s AND %(hi)s
            ORDER BY effective_at DESC LIMIT 1
        ) p
        GROUP BY 1 HAVING count(*) >= 3 ORDER BY 1
        """,
        {"days": days, "ids": ids, "grade": grade, "lo": PRICE_RANGE[0], "hi": PRICE_RANGE[1]},
    ).fetchall()
    out["daily"] = [
        {"day": d, "median": round(m, 1), "min": round(lo, 1), "stations": n}
        for d, m, lo, n in rows
    ]
    if len(rows) >= 8:
        out["change_7d"] = round(rows[-1][1] - rows[-8][1], 1)
    kind = "diesel" if grade.startswith("B") or grade == "HVO" else "petrol"
    uk = conn.execute(
        "SELECT price FROM uk_average_fuel_prices WHERE fuel = %s ORDER BY week_start DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if uk and rows:
        out["uk_avg"] = round(uk[0], 1)
        out["vs_uk"] = round(rows[-1][1] - uk[0], 1)
    return out


SAVINGS_RADIUS_KM = 5


def missed_savings(conn: psycopg.Connection, fills: list[dict], l100: float = 7.0) -> None:
    """For each priced fill-up, the best-value station near it that day and what it saves.

    The saving counts the fuel to drive there and back from where you filled, so a
    slightly cheaper station across town doesn't count. Sets cheapest: {name, price, km}
    and could_save_gbp (0 when where you went was the best value). Needs a position
    and the price per litre paid.
    """
    for f in fills:
        f["cheapest"] = f["could_save_gbp"] = None
        if f["lat"] is None or not f["price_ppl"] or not f["litres_added"]:
            continue
        grade = f["fuel_grade"] or "E10"
        dlat = SAVINGS_RADIUS_KM / 111.32
        dlon = dlat / _cos(f["lat"])
        row = conn.execute(
            """
            WITH near AS (
                SELECT node_id, trading_name,
                       6371 * sqrt(power(radians(latitude - %(lat)s), 2)
                         + power(radians(longitude - %(lon)s) * cos(radians(%(lat)s)), 2)) AS km
                FROM forecourts
                WHERE latitude BETWEEN %(lat)s - %(dlat)s AND %(lat)s + %(dlat)s
                  AND longitude BETWEEN %(lon)s - %(dlon)s AND %(lon)s + %(dlon)s
            )
            SELECT n.trading_name, p.price, n.km FROM near n
            CROSS JOIN LATERAL (
                SELECT price FROM fuel_prices
                WHERE node_id = n.node_id AND fuel_type = %(grade)s
                  AND effective_at <= %(t)s AND effective_at > %(t)s - interval '14 days'
                ORDER BY effective_at DESC LIMIT 1
            ) p
            WHERE n.km <= %(r)s AND p.price BETWEEN %(lo)s AND %(hi)s
            ORDER BY p.price, n.km LIMIT 15
            """,
            {
                "lat": f["lat"],
                "lon": f["lon"],
                "dlat": dlat,
                "dlon": dlon,
                "grade": grade,
                "t": f["time"],
                "r": SAVINGS_RADIUS_KM,
                "lo": PRICE_RANGE[0],
                "hi": PRICE_RANGE[1],
            },
        ).fetchall()
        best, best_save = None, 0.0
        for name, price, km in row:
            detour = 2 * km * l100 / 100 * price / 100
            save = (f["price_ppl"] - price) * f["litres_added"] / 100 - detour
            if save > best_save:
                best, best_save = (name, price, km), save
        f["could_save_gbp"] = round(best_save, 2) if best else 0.0 if row else None
        if best:
            f["cheapest"] = {"name": tidy_name(best[0]), "price": best[1], "km": round(best[2], 1)}


def glance(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict:
    """The money-and-economy headline for the Overview: what driving really costs you."""
    from . import economy, gauge

    now = now or datetime.now(UTC)
    out = {
        "economy_l100": None,
        "economy_source": None,
        "car_estimate_l100": None,
        "real_range_km": None,
        "range_km": None,
        "price": None,
        "pence_per_km": None,
        "week_km": None,
        "week_cost_gbp": None,
        "month_km": None,
        "month_cost_gbp": None,
    }
    if not _ready(conn):
        return out
    plan = economy.best(conn, vin, fillup_economy(fill_intervals(conn, vin)))
    g = gauge.gauge_economy(conn, vin, now)
    price = fuel_price(conn, vin)
    if g:
        out["car_estimate_l100"] = (g["car_estimate"] or {}).get("l100")
        out["real_range_km"] = g["real_range_km"]
        out["range_km"] = g["range_now_km"]
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_km, month_km = conn.execute(
        "SELECT COALESCE(sum(distance_km) FILTER (WHERE start_time >= %s), 0),"
        " COALESCE(sum(distance_km) FILTER (WHERE start_time >= %s), 0)"
        " FROM trips WHERE vin = %s",
        (week_start, month_start, vin),
    ).fetchone()
    out["week_km"], out["month_km"] = round(week_km, 1), round(month_km, 1)
    if plan:
        out["economy_l100"], out["economy_source"] = plan["l100"], plan["source"]
    if plan and price:
        ppk = plan["l100"] / 100 * price["ppl"]
        out["price"] = price
        out["pence_per_km"] = round(ppk, 1)
        out["week_cost_gbp"] = round(week_km * ppk / 100, 2)
        out["month_cost_gbp"] = round(month_km * ppk / 100, 2)
    return out
