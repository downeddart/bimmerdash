"""Refuel advisor: where to fill up next, counting the fuel it takes to get there.

For each station near the car: the cost of filling the tank there, plus the fuel burned
driving there and back (as a round trip from where the car is, which overstates the
detour for stations on your way). Ranked by that total; the saving is against the
nearest station, which wins unless another saves at least WORTH_IT.
"""

from __future__ import annotations

import psycopg

from . import economy
from . import keys as K
from .derived import _grade, stations_near
from .health import _num
from .queries import _measure, _val, latest

DEFAULT_L100 = 7.0  # only until the car has reported its own economy
MAX_RADIUS_KM = 15
RANGE_SHARE = 0.4  # never suggest a station further than this share of the range left
WORTH_IT = 0.50  # £: a smaller saving than this isn't worth a different station


def _tank(conn: psycopg.Connection, vin: str) -> float | None:
    row = conn.execute(
        "SELECT COALESCE((SELECT (value #>> '{}')::float FROM settings"
        "  WHERE scope = %s AND key = 'tank_litres'), tank_litres)"
        " FROM vehicles WHERE vin = %s",
        (vin, vin),
    ).fetchone()
    return row[0] if row and row[0] else None


def advise(conn: psycopg.Connection, vin: str, radius_km: float = 8) -> dict:
    lv = latest(conn, vin)
    lat, lon = _num(_val(lv, K.LATITUDE)), _num(_val(lv, K.LONGITUDE))
    if lat is not None and lon is not None and (abs(lat) > 90 or abs(lon) > 180):
        lat = lon = None
    pct = _val(lv, K.FUEL_PERCENT)
    litres_m = _measure(lv, K.FUEL_LITRES)
    range_m = _measure(lv, K.RANGE)
    # The car sends placeholders such as "-"; treat anything unreadable as missing.
    litres = _num(litres_m["value"]) if litres_m else None
    rv = _num(range_m["value"]) if range_m else None
    range_km = None
    if rv is not None:
        range_km = rv * (
            economy.KM_PER_MILE if (range_m["unit"] or "").lower() in ("mi", "miles") else 1
        )
    tank = _tank(conn, vin)
    pct = _num(pct)
    if litres is None and pct is not None and tank:
        litres = tank * pct / 100
    grade = _grade(conn, vin)
    econ = economy.best(conn, vin)
    l100 = econ["l100"] if econ else DEFAULT_L100

    out = {
        "grade": grade,
        "fuel": {"percent": pct, "litres": litres, "range_km": range_km, "tank_litres": tank},
        "economy": {"l100": l100, "source": econ["source"] if econ else "assumed"},
        "fill_litres": None,
        "radius_km": None,
        "stations": [],
        "best": None,
        "nearest": None,
        "position": None,
    }
    if lat is None or lon is None:
        return out
    out["position"] = {"lat": lat, "lon": lon}
    radius = min(radius_km, MAX_RADIUS_KM)
    if range_km:
        radius = max(1.0, min(radius, range_km * RANGE_SHARE))
    out["radius_km"] = round(radius, 1)
    fill = round(tank - litres, 1) if tank and litres is not None else None
    out["fill_litres"] = fill if fill and fill > 0 else None
    # Without a fill size, compare on a typical 40 L top-up.
    litres_to_buy = out["fill_litres"] or 40.0

    stations = stations_near(conn, lat, lon, grade, radius, limit=40)
    if not stations:
        return out
    for s in stations:
        detour_l = 2 * s["distance_km"] * l100 / 100
        s["fill_cost"] = round(litres_to_buy * s["price"] / 100, 2)
        s["detour_cost"] = round(detour_l * s["price"] / 100, 2)
        s["total_cost"] = round(s["fill_cost"] + s["detour_cost"], 2)
    nearest = min(stations, key=lambda s: s["distance_km"])
    for s in stations:
        s["saving"] = round(nearest["total_cost"] - s["total_cost"], 2)
    ranked = sorted(stations, key=lambda s: (s["total_cost"], s["distance_km"]))
    best = ranked[0]
    if best["saving"] < WORTH_IT:
        best = nearest  # pennies: just go to the nearest
        ranked.remove(nearest)
        ranked.insert(0, nearest)
    out["stations"] = ranked[:8]
    out["best"] = best
    out["nearest"] = nearest
    out["litres_compared"] = litres_to_buy
    return out
