"""Fuel economy from the car's own lifetime counters, and the best figure to plan with.

The car keeps two running totals, fuel burned and distance driven, which the collector
polls a few times a day. The change in each between two polls is the economy over that
stretch, to a tenth of a litre: far better than the tank gauge (whole litres) allows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

FUEL = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel"
DISTANCE = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.referenceDistance"
KM_PER_MILE = 1.609344
MIN_KM = 50  # shorter stretches say more about the last trip than about the car
PAIR_WITHIN = timedelta(hours=1)
RECENT_DAYS = 30


def _snapshots(conn: psycopg.Connection, vin: str) -> list[tuple[datetime, float, float]]:
    """(time, litres, km) pairs, oldest first."""
    rows = conn.execute(
        "SELECT key, time, value_num, unit FROM telemetry"
        " WHERE vin = %s AND key = ANY(%s) AND value_num IS NOT NULL ORDER BY time",
        (vin, [FUEL, DISTANCE]),
    ).fetchall()
    fuel = [(t, v) for k, t, v, _ in rows if k == FUEL]
    dist = [
        (t, v * KM_PER_MILE if (u or "").lower() in ("mi", "miles") else v)
        for k, t, v, u in rows
        if k == DISTANCE
    ]
    out = []
    j = 0
    for t, km in dist:
        # nearest fuel reading in time
        while j + 1 < len(fuel) and abs(fuel[j + 1][0] - t) <= abs(fuel[j][0] - t):
            j += 1
        if fuel and abs(fuel[j][0] - t) <= PAIR_WITHIN:
            out.append((t, fuel[j][1], km))
    return out


def _l100(litres: float, km: float) -> float | None:
    return round(100 * litres / km, 2) if km and km > 0 and litres >= 0 else None


def car_economy(conn: psycopg.Connection, vin: str, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(UTC)
    snaps = _snapshots(conn, vin)
    if not snaps:
        return None
    last_t, last_l, last_km = snaps[-1]
    out = {
        "lifetime_l100": _l100(last_l, last_km),
        "lifetime_km": round(last_km),
        "lifetime_litres": round(last_l),
        "as_of": last_t,
        "recent_l100": None,
        "recent_km": None,
        "recent_since": None,
        "monthly": [],
    }
    # Recent: from the last snapshot at least RECENT_DAYS old, else the oldest we have.
    cutoff = now - timedelta(days=RECENT_DAYS)
    base = next((s for s in reversed(snaps) if s[0] <= cutoff), snaps[0])
    km = last_km - base[2]
    if km >= MIN_KM:
        out["recent_l100"] = _l100(last_l - base[1], km)
        out["recent_km"] = round(km, 1)
        out["recent_since"] = base[0]
    # Per month: last snapshot of each month against the one before.
    month_end: dict[str, tuple] = {}
    for s in snaps:
        month_end[s[0].strftime("%Y-%m")] = s
    prev = snaps[0]
    for m, s in sorted(month_end.items()):
        km = s[2] - prev[2]
        if km >= MIN_KM:
            out["monthly"].append(
                {"month": m, "l100": _l100(s[1] - prev[1], km), "km": round(km, 1)}
            )
        prev = s
    out["monthly"] = out["monthly"][-12:]
    return out


def best(conn: psycopg.Connection, vin: str, fillup_l100: float | None = None) -> dict | None:
    """The economy to plan costs with: {l100, source}."""
    car = car_economy(conn, vin)
    if car and car["recent_l100"]:
        return {"l100": car["recent_l100"], "source": "car_recent"}
    if fillup_l100:
        return {"l100": fillup_l100, "source": "fillups"}
    if car and car["lifetime_l100"]:
        return {"l100": car["lifetime_l100"], "source": "car_lifetime"}
    return None
