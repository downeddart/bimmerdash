"""Fuel economy from the fuel gauge and the range readout.

Many cars send neither a consumption figure nor lifetime fuel counters, only the fuel
level and the range left. That is still enough:

* **Measured economy.** The gauge's percent reading moves in steps of about half a litre
  (finer than the whole-litre reading), so the fuel it drops over 50 km or more, divided
  by the distance on the odometer, is your real economy to within a few percent. Each
  tank between fill-ups is measured separately.
* **The car's own estimate.** The range readout is the fuel left divided by the economy
  the car expects from your recent driving, so fuel / range gives that economy back.
* **Range check.** How much range the readout used up for each km you actually drove.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

from . import keys as K

KM_PER_MILE = 1.609344
REFUEL_PCT = 8  # a rise this big between readings is a fill-up, not the gauge settling
REFUEL_L = 4
MIN_KM = 40  # a shorter stretch is mostly gauge rounding
MIN_LITRES = 2.5
RECENT_DAYS = 30
GAUGE_STEP_PCT = 1  # the percent reading's resolution


@dataclass
class Reading:
    time: datetime
    odo_km: float | None
    pct: float | None
    litres: float | None
    range_km: float | None
    fuel_l: float | None = None  # best fuel figure: percent of the tank, else litres


def _km(value: float, unit: str | None) -> float:
    return value * KM_PER_MILE if (unit or "").lower() in ("mi", "miles") else value


def tank_litres(conn: psycopg.Connection, vin: str) -> tuple[float | None, str | None]:
    """The tank size: the owner's setting or the car's details, else what the gauge implies.

    Every reading pairs whole litres with a percent; litres / percent across many
    readings recovers the usable tank size to a fraction of a litre.
    """
    row = conn.execute(
        "SELECT COALESCE((SELECT (value #>> '{}')::float FROM settings"
        "  WHERE scope = %s AND key = 'tank_litres'), tank_litres)"
        " FROM vehicles WHERE vin = %s",
        (vin, vin),
    ).fetchone()
    if row and row[0]:
        return float(row[0]), "setting"
    rows = conn.execute(
        "SELECT l.value_num, p.value_num FROM telemetry l"
        " JOIN telemetry p ON p.vin = l.vin AND p.time = l.time AND p.key = ANY(%s)"
        " WHERE l.vin = %s AND l.key = ANY(%s) AND p.value_num >= 20 AND l.value_num > 0"
        " ORDER BY l.time DESC LIMIT 500",
        (K.FUEL_PERCENT, vin, K.FUEL_LITRES),
    ).fetchall()
    if len(rows) < 5:
        return None, None
    return round(statistics.median(lit / pct * 100 for lit, pct in rows), 1), "gauge"


def readings(
    conn: psycopg.Connection, vin: str, tank: float | None, since: datetime | None = None
) -> list[Reading]:
    """One reading per message time, each value carried forward from its last report."""
    keys = [K.ODOMETER[0], K.FUEL_PERCENT[0], K.FUEL_LITRES[0], *K.RANGE]
    rows = conn.execute(
        "SELECT time, key, value_num, unit FROM telemetry"
        " WHERE vin = %s AND key = ANY(%s) AND value_num IS NOT NULL"
        " AND time >= COALESCE(%s, '-infinity'::timestamptz) ORDER BY time",
        (vin, keys, since),
    ).fetchall()
    out: list[Reading] = []
    cur = {"odo": None, "pct": None, "lit": None, "rng": None}
    rng_key = None  # first range key the car actually sends
    for i, (t, key, v, unit) in enumerate(rows):
        if key == K.ODOMETER[0]:
            cur["odo"] = _km(v, unit)
        elif key == K.FUEL_PERCENT[0]:
            cur["pct"] = v
        elif key == K.FUEL_LITRES[0]:
            cur["lit"] = v
        elif rng_key in (None, key):
            rng_key = key
            cur["rng"] = _km(v, unit)
        if i + 1 < len(rows) and rows[i + 1][0] == t:
            continue  # more values in this message
        r = Reading(t, cur["odo"], cur["pct"], cur["lit"], cur["rng"])
        if r.pct is not None and tank:
            r.fuel_l = r.pct * tank / 100
        else:
            r.fuel_l = r.litres
        if r.odo_km is not None and r.fuel_l is not None:
            out.append(r)
    return out


def tanks(rs: list[Reading]) -> list[list[Reading]]:
    """Split readings into tanks: a big rise in fuel starts a new one."""
    out: list[list[Reading]] = []
    for r in rs:
        if out:
            prev = out[-1][-1]
            rise_pct = (r.pct - prev.pct) if r.pct is not None and prev.pct is not None else None
            rise_l = r.fuel_l - prev.fuel_l
            if (rise_pct is not None and rise_pct >= REFUEL_PCT) or (
                rise_pct is None and rise_l >= REFUEL_L
            ):
                out.append([r])
                continue
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _stretch(seg: list[Reading]) -> dict | None:
    """Fuel used and distance over one tank (or part of one)."""
    if len(seg) < 2:
        return None
    a, b = seg[0], seg[-1]
    km = b.odo_km - a.odo_km
    # The gauge can wobble up by a step on a slope; the first reading after the fill
    # may also not have settled. Measure from the highest of the first few readings.
    start = max(r.fuel_l for r in seg[: min(len(seg), 4)])
    litres = start - b.fuel_l
    out = {
        "start": a.time,
        "end": b.time,
        "km": round(km, 1),
        "litres": round(litres, 1),
        "l100": round(100 * litres / km, 1) if km >= MIN_KM and litres >= MIN_LITRES else None,
        # Rounding at each end: one gauge step (or a litre) either way.
        "plus_minus_pct": None,
        "range_used_km": None,
    }
    if out["l100"]:
        step = (a.fuel_l / a.pct * GAUGE_STEP_PCT) if a.pct else 1.0
        out["plus_minus_pct"] = round(100 * step / litres)
    ranges = [r for r in seg if r.range_km is not None]
    if len(ranges) >= 2 and km > 0:
        out["range_used_km"] = round(ranges[0].range_km - ranges[-1].range_km, 1)
    return out


def car_estimate(rs: list[Reading], days: int = 60) -> dict | None:
    """The economy the car's range readout assumes: fuel left / range left."""
    pts = [
        (r.time, 100 * r.fuel_l / r.range_km)
        for r in rs
        if r.range_km and r.range_km >= 30 and r.fuel_l and r.fuel_l >= 3
    ]
    pts = [(t, v) for t, v in pts if 2 <= v <= 30]  # ignore placeholder readings
    if not pts:
        return None
    by_day: dict[str, list[float]] = {}
    for t, v in pts:
        by_day.setdefault(t.date().isoformat(), []).append(v)
    daily = [{"day": d, "l100": round(statistics.median(v), 2)} for d, v in sorted(by_day.items())][
        -days:
    ]
    return {"l100": round(pts[-1][1], 1), "as_of": pts[-1][0], "daily": daily}


def gauge_economy(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(UTC)
    tank, tank_source = tank_litres(conn, vin)
    rs = readings(conn, vin, tank, since=now - timedelta(days=400))
    if not rs:
        return None
    segs = tanks(rs)
    stretches = [s for s in (_stretch(seg) for seg in segs) if s]
    current = stretches[-1] if stretches and stretches[-1]["end"] == rs[-1].time else None
    done = [s for s in stretches if s is not current]
    # Recent: every tank (or part) that ended in the last RECENT_DAYS, pooled.
    cutoff = now - timedelta(days=RECENT_DAYS)
    pool = [s for s in stretches if s["end"] >= cutoff and s["km"] > 0 and s["litres"] > 0]
    km = sum(s["km"] for s in pool)
    litres = sum(s["litres"] for s in pool)
    recent = (
        {"l100": round(100 * litres / km, 1), "km": round(km, 1), "litres": round(litres, 1)}
        if km >= MIN_KM and litres >= MIN_LITRES
        else None
    )
    ranged = [s for s in pool if s["range_used_km"] is not None]
    rkm = sum(s["km"] for s in ranged)
    range_check = (
        {
            "km": round(rkm, 1),
            "range_used_km": round(sum(s["range_used_km"] for s in ranged), 1),
            "per_km": round(sum(s["range_used_km"] for s in ranged) / rkm, 2),
        }
        if rkm >= MIN_KM
        else None
    )
    last = rs[-1]
    best_l100 = (current or {}).get("l100") or (recent or {}).get("l100")
    return {
        "tank_litres": tank,
        "tank_source": tank_source,
        "fuel_now_l": round(last.fuel_l, 1),
        "range_now_km": last.range_km,
        # What the fuel left really gets you, at the economy you've measured.
        "real_range_km": round(100 * last.fuel_l / best_l100) if best_l100 else None,
        "current_tank": current,
        "recent": recent,
        "tanks": [s for s in done if s["l100"]][-12:],
        "car_estimate": car_estimate(rs),
        "range_check": range_check,
    }
