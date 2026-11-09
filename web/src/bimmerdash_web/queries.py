"""Read-only queries over the collector's tables."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from . import keys as K

Latest = dict[str, dict[str, Any]]


def vehicles(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT vin, model_name, series, body_type, colour, image IS NOT NULL, first_seen"
        " FROM vehicles ORDER BY first_seen"
    ).fetchall()
    return [
        {
            "vin": r[0],
            "model_name": r[1],
            "series": r[2],
            "body_type": r[3],
            "colour": _colour(r[4]),
            "has_image": r[5],
            "first_seen": r[6],
        }
        for r in rows
    ]


def vehicle_image(conn: psycopg.Connection, vin: str) -> tuple[bytes, str] | None:
    row = conn.execute("SELECT image, image_type FROM vehicles WHERE vin = %s", (vin,)).fetchone()
    if not row or row[0] is None:
        return None
    return bytes(row[0]), row[1] or "image/png"


def latest(conn: psycopg.Connection, vin: str) -> Latest:
    rows = conn.execute(
        "SELECT key, value, unit, time FROM latest_values WHERE vin = %s ORDER BY key", (vin,)
    ).fetchall()
    return {r[0]: {"value": r[1], "unit": r[2], "time": r[3]} for r in rows}


def _pick(lv: Latest, candidates: list[str]) -> dict | None:
    for k in candidates:
        if k in lv and lv[k]["value"] is not None:
            return lv[k]
    return None


def _val(lv: Latest, candidates: list[str]):
    p = _pick(lv, candidates)
    return None if p is None else p["value"]


def _measure(lv: Latest, candidates: list[str]) -> dict | None:
    p = _pick(lv, candidates)
    return None if p is None else {"value": p["value"], "unit": p["unit"]}


def overview(lv: Latest) -> dict:
    """Interpret the latest values. Anything the car hasn't sent is None."""
    lat, lon = _val(lv, K.LATITUDE), _val(lv, K.LONGITUDE)
    windows = [lv[k]["value"] for k in K.WINDOWS if k in lv]
    tyres = {pos: _measure(lv, [key]) for pos, key in K.TYRES.items()}
    cc = _val(lv, K.CHECK_CONTROL)
    return {
        "updated_at": max((v["time"] for v in lv.values()), default=None),
        "odometer": _measure(lv, K.ODOMETER),
        "fuel_percent": _val(lv, K.FUEL_PERCENT),
        "fuel_litres": _measure(lv, K.FUEL_LITRES),
        "range": _measure(lv, K.RANGE),
        "location": (
            {"lat": lat, "lon": lon, "heading": _val(lv, K.HEADING)}
            if lat is not None and lon is not None
            else None
        ),
        "heading": _val(lv, K.HEADING),
        "door_lock": _val(lv, K.DOOR_LOCK),
        "doors": _val(lv, K.DOORS),
        "trunk_open": _val(lv, K.TRUNK_OPEN),
        "windows_closed": all(str(w).upper() == "CLOSED" for w in windows) if windows else None,
        "tyres": tyres if any(tyres.values()) else None,
        "service_distance": _measure(lv, K.SERVICE_DISTANCE),
        "check_control_count": len(cc) if isinstance(cc, list) else None,
        "is_moving": _val(lv, K.IS_MOVING),
        "battery_12v": _measure(lv, K.BATTERY_12V),
    }


def weekly_distance(conn: psycopg.Connection, vin: str, weeks: int = 8) -> list[dict]:
    """Distance per ISO week from odometer readings (last reading of each week, differenced)."""
    rows = conn.execute(
        """
        WITH w AS (
            SELECT time_bucket('7 days', time, TIMESTAMPTZ '2000-01-03') AS week,
                   min(value_num) AS first_km, max(value_num) AS last_km
            FROM telemetry
            WHERE vin = %s AND key = ANY(%s) AND value_num IS NOT NULL
              AND time > now() - make_interval(weeks => %s + 1)
            GROUP BY 1
        )
        SELECT week,
               last_km - COALESCE(lag(last_km) OVER (ORDER BY week), first_km) AS km
        FROM w ORDER BY week DESC LIMIT %s
        """,
        (vin, K.ODOMETER, weeks, weeks),
    ).fetchall()
    return [{"week_start": r[0], "km": round(r[1], 1)} for r in reversed(rows)]


def stream_status(conn: psycopg.Connection) -> dict:
    row = conn.execute(
        """
        SELECT (SELECT max(received_at) FROM raw_messages),
               (SELECT count(*) FROM raw_messages WHERE received_at > now() - interval '1 hour'),
               (SELECT count(*) FROM api_calls WHERE time > now() - interval '24 hours')
        """
    ).fetchone()
    return {
        "last_message_at": row[0],
        "messages_last_hour": row[1],
        "api_calls_24h": row[2],
        "prices": price_freshness(conn),
    }


# DESNZ publishes each Monday/Tuesday for the previous Monday, so the newest week is
# normally 7-9 days old. Station prices sync every 30 minutes.
UK_AVERAGE_MAX_AGE = timedelta(days=10)
UK_AVERAGE_MAX_CHECK_AGE = timedelta(days=2)
STATION_MAX_AGE = timedelta(hours=2)


def classify_freshness(
    latest_week: date | None,
    averages_checked_at: datetime | None,
    stations_synced_at: datetime | None,
    now: datetime,
) -> dict:
    """ok / stale / missing (UK averages) and ok / stale / off (station prices)."""
    if latest_week is None:
        uk = "missing"
    elif now.date() - latest_week > UK_AVERAGE_MAX_AGE or (
        averages_checked_at is None or now - averages_checked_at > UK_AVERAGE_MAX_CHECK_AGE
    ):
        uk = "stale"
    else:
        uk = "ok"
    if stations_synced_at is None:
        stations = "off"
    elif now - stations_synced_at > STATION_MAX_AGE:
        stations = "stale"
    else:
        stations = "ok"
    return {
        "uk_averages": {
            "status": uk,
            "latest_week": latest_week,
            "checked_at": averages_checked_at,
        },
        "station_prices": {"status": stations, "synced_at": stations_synced_at},
    }


def price_freshness(conn: psycopg.Connection, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    present = conn.execute(
        "SELECT to_regclass('uk_average_fuel_prices') IS NOT NULL,"
        " to_regclass('fuel_finder_state') IS NOT NULL"
    ).fetchone()
    latest_week = checked = synced = None
    if present[0]:
        latest_week = conn.execute("SELECT max(week_start) FROM uk_average_fuel_prices").fetchone()[
            0
        ]
    if present[0]:  # migration 003 adds the averages table and uk_averages_at together
        row = conn.execute(
            "SELECT uk_averages_at, incremental_at FROM fuel_finder_state"
        ).fetchone()
        if row:
            checked, synced = row
    elif present[1]:
        row = conn.execute("SELECT incremental_at FROM fuel_finder_state").fetchone()
        synced = row[0] if row else None
    return classify_freshness(latest_week, checked, synced, now)


def _colour(raw: str | None) -> str | None:
    """BMW sends German upper-case paint names, e.g. MINERALGRAU METALLIC."""
    if not raw:
        return None
    names = {
        "MINERALGRAU": "Mineral Grey",
        "SAPHIRSCHWARZ": "Sapphire Black",
        "ALPINWEISS": "Alpine White",
        "PORTIMAO BLAU": "Portimao Blue",
        "SKYSCRAPER GRAU": "Skyscraper Grey",
        "MINERALWEISS": "Mineral White",
        "BROOKLYN GRAU": "Brooklyn Grey",
        "TANZANITBLAU": "Tanzanite Blue",
        "MELBOURNE ROT": "Melbourne Red",
        "DRAVITGRAU": "Dravit Grey",
        "SCHWARZ": "Black",
    }  # most specific names first
    text = raw.upper()
    for de, en in names.items():
        if text.startswith(de):
            return en
    return raw.title()
