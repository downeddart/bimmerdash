"""The weekly summary: last week's driving and what it cost, pushed on Monday morning.

The car doesn't report its fuel use, so fuel comes from the gauge: every drop in the
percent reading over the week (rises of REFUEL_PCT or more are fill-ups and skipped),
times the tank size. That's about half a litre per step, close enough over a week.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from .alerts import KM_PER_MILE, Alert, cheapest_near

log = logging.getLogger(__name__)

FUEL_PERCENT = "vehicle.drivetrain.fuelSystem.level"
FUEL_LITRES = "vehicle.drivetrain.fuelSystem.remainingFuel"
REFUEL_PCT = 8
SEND_HOUR_UTC = 8  # Monday, 8 or 9 in the morning in the UK
STATE_KEY = "digest_week"


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Last full week, Monday 00:00 to Monday 00:00 (UTC)."""
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return this_monday - timedelta(days=7), this_monday


def _tank(conn, vin: str) -> float | None:
    row = conn.execute(
        "SELECT COALESCE((SELECT (value #>> '{}')::float FROM settings"
        "  WHERE scope = %s AND key = 'tank_litres'), tank_litres)"
        " FROM vehicles WHERE vin = %s",
        (vin, vin),
    ).fetchone()
    if row and row[0]:
        return float(row[0])
    rows = conn.execute(
        "SELECT l.value_num / p.value_num * 100 FROM telemetry l"
        " JOIN telemetry p ON p.vin = l.vin AND p.time = l.time AND p.key = %s"
        " WHERE l.vin = %s AND l.key = %s AND p.value_num >= 20 AND l.value_num > 0"
        " ORDER BY l.time DESC LIMIT 200",
        (FUEL_PERCENT, vin, FUEL_LITRES),
    ).fetchall()
    return statistics.median(r[0] for r in rows) if len(rows) >= 5 else None


def fuel_used(conn, vin: str, start: datetime, end: datetime) -> float | None:
    """Litres burned between start and end, from drops in the gauge's percent reading."""
    tank = _tank(conn, vin)
    if not tank:
        return None
    pcts = [
        r[0]
        for r in conn.execute(
            "SELECT value_num FROM telemetry WHERE vin = %s AND key = %s"
            " AND value_num IS NOT NULL AND time >= %s AND time < %s ORDER BY time",
            (vin, FUEL_PERCENT, start, end),
        )
    ]
    used = 0.0
    for a, b in zip(pcts, pcts[1:], strict=False):
        if b < a:
            used += a - b
        elif b - a < REFUEL_PCT:
            used -= b - a  # the gauge settling back up a step
    return max(0.0, used) * tank / 100


def _price(conn, vin: str) -> tuple[float, bool] | None:
    """Pence per litre you've paid lately, else the UK average (flagged as such)."""
    row = conn.execute(
        "SELECT sum(price_ppl * litres_added) / sum(litres_added) FROM ("
        " SELECT price_ppl, litres_added FROM fillups WHERE vin = %s"
        " AND price_ppl IS NOT NULL AND litres_added > 0 ORDER BY time DESC LIMIT 5) f",
        (vin,),
    ).fetchone()
    if row and row[0]:
        return float(row[0]), False
    row = conn.execute(
        "SELECT price FROM uk_average_fuel_prices WHERE fuel = 'petrol'"
        " ORDER BY week_start DESC LIMIT 1"
    ).fetchone()
    return (float(row[0]), True) if row else None


def _home(conn, vin: str) -> tuple[float, float] | None:
    row = conn.execute(
        "SELECT avg(end_lat), avg(end_lon), count(*) FROM trips"
        " WHERE vin = %s AND end_lat IS NOT NULL"
        " GROUP BY round(end_lat::numeric, 3), round(end_lon::numeric, 3)"
        " ORDER BY count(*) DESC LIMIT 1",
        (vin,),
    ).fetchone()
    return (row[0], row[1]) if row and row[2] >= 2 else None


def _units(conn) -> tuple[str, str]:
    rows = dict(
        conn.execute(
            "SELECT key, value #>> '{}' FROM settings WHERE scope = 'global'"
            " AND key IN ('distance_unit', 'economy_unit')"
        ).fetchall()
    )
    return rows.get("distance_unit") or "mi", rows.get("economy_unit") or "mpg"


def build(conn, vin: str, now: datetime) -> Alert | None:
    start, end = week_bounds(now)
    trips, km = conn.execute(
        "SELECT count(*), COALESCE(sum(distance_km), 0) FROM trips"
        " WHERE vin = %s AND start_time >= %s AND start_time < %s",
        (vin, start, end),
    ).fetchone()
    if not trips:
        return None
    prev_km = conn.execute(
        "SELECT COALESCE(sum(distance_km), 0) FROM trips"
        " WHERE vin = %s AND start_time >= %s AND start_time < %s",
        (vin, start - timedelta(days=7), start),
    ).fetchone()[0]
    dist_unit, econ_unit = _units(conn)

    def d(v: float) -> str:
        return f"{v / KM_PER_MILE:,.0f} mi" if dist_unit == "mi" else f"{v:,.0f} km"

    lines = [f"{d(km)} in {trips} trip{'s' if trips != 1 else ''}"]
    if prev_km:
        change = round(100 * (km / prev_km - 1))
        lines[0] += f" ({'+' if change >= 0 else ''}{change}% on the week before)"
    litres = fuel_used(conn, vin, start, end)
    price = _price(conn, vin)
    if litres and litres >= 1:
        cost = f" ≈ £{litres * price[0] / 100:.2f}" if price else ""
        econ = ""
        if km >= 40:
            l100 = 100 * litres / km
            econ = f", {282.481 / l100:.0f} mpg" if econ_unit == "mpg" else f", {l100:.1f} L/100 km"
        lines.append(f"Fuel: {litres:.1f} L{cost}{econ}")
    fills = conn.execute(
        # A receipt entered in the web UI (matched within 6 hours) beats the estimate.
        "SELECT count(*), sum(COALESCE((SELECT r.cost_gbp FROM fillup_receipts r"
        "  WHERE r.vin = f.vin AND r.fillup_time BETWEEN f.time - interval '6 hours'"
        "  AND f.time + interval '6 hours'"
        "  ORDER BY abs(extract(epoch FROM r.fillup_time - f.time)) LIMIT 1), f.cost_gbp))"
        " FROM fillups f WHERE f.vin = %s AND f.time >= %s AND f.time < %s",
        (vin, start, end),
    ).fetchone()
    if fills[0]:
        spent = f", £{fills[1]:.2f}" if fills[1] else ""
        lines.append(f"Filled up {fills[0]}×{spent}")
    home = _home(conn, vin)
    if home:
        grade = conn.execute(
            "SELECT value #>> '{}' FROM settings WHERE scope = %s AND key = 'fuel_grade'", (vin,)
        ).fetchone()
        best = cheapest_near(conn, *home, grade[0] if grade and grade[0] else "E10", 8)
        if best:
            lines.append(
                f"Cheapest near home: {best['name']} {best['price']:.1f}p ({d(best['km'])} away)"
            )
    return Alert("digest", start.date().isoformat(), "Your week in the car", "\n".join(lines))


def run(db, notifier, now: datetime | None = None) -> int:
    """Send last week's summary once, on Monday morning. Returns how many were sent."""
    now = now or datetime.now(UTC)
    conn = db.conn
    if not notifier.configured or now.weekday() != 0 or now.hour < SEND_HOUR_UTC:
        return 0
    from .alerts import settings

    cfg = settings(conn)
    if not cfg["enabled"] or not cfg.get("weekly_digest", True):
        conn.commit()
        return 0
    week = week_bounds(now)[0].date().isoformat()
    row = conn.execute("SELECT value FROM collector_state WHERE key = %s", (STATE_KEY,)).fetchone()
    if row and row[0] == week:
        conn.commit()
        return 0
    sent = 0
    for (vin,) in conn.execute("SELECT vin FROM vehicles").fetchall():
        try:
            alert = build(conn, vin, now)
            if alert:
                notifier.send(alert)
                sent += 1
        except Exception as e:  # one car's summary must not stop the others
            log.warning("weekly summary failed: %s", e.__class__.__name__)
            conn.rollback()
    # Mark the week done even if a send failed: a summary is not worth retrying all day.
    conn.execute(
        "INSERT INTO collector_state (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (STATE_KEY, Jsonb(week)),
    )
    conn.commit()
    return sent
