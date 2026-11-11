"""User settings, stored in the collector's `settings` table.

Display units are global (everyone viewing sees the same units). Fuel grade and tank size
are per car; the collector recalculates fill-up costs within a minute of a change.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

GRADES = {
    "E10": "E10 unleaded (standard petrol)",
    "E5": "E5 super unleaded",
    "B7_STANDARD": "Diesel",
    "B7_PREMIUM": "Premium diesel",
}
# Kept in step with the collector's alerts.DEFAULTS.
ALERT_DEFAULTS = {
    "enabled": True,
    "left_open": True,
    "left_open_minutes": 10,
    "low_fuel": True,
    "low_fuel_percent": 20,
    "tyres": True,
    "tyre_low_percent": 10,
    "service": True,
    "service_days": 30,
    "warnings": True,
}
DEFAULTS = {"distance_unit": "mi", "economy_unit": "mpg", "alerts": ALERT_DEFAULTS}
# Saving these as 0 or "" removes them (back to automatic / not set).
CLEARABLE = {"tank_litres", "mileage_limit_km", "mileage_year_start"}


class AlertSettings(BaseModel):
    enabled: bool | None = None
    left_open: bool | None = None
    left_open_minutes: int | None = Field(None, ge=5, le=240)
    low_fuel: bool | None = None
    low_fuel_percent: int | None = Field(None, ge=5, le=50)
    tyres: bool | None = None
    tyre_low_percent: int | None = Field(None, ge=5, le=30)
    service: bool | None = None
    service_days: int | None = Field(None, ge=7, le=120)
    warnings: bool | None = None


class GlobalSettings(BaseModel):
    distance_unit: str | None = Field(None, pattern="^(km|mi)$")
    economy_unit: str | None = Field(None, pattern="^(mpg|l100)$")
    alerts: AlertSettings | None = None


class VehicleSettings(BaseModel):
    fuel_grade: str | None = Field(None, pattern="^(E10|E5|B7_STANDARD|B7_PREMIUM)$")
    # 0 clears it (use what the car reports)
    tank_litres: float | None = Field(None, ge=0, le=200)
    # Yearly mileage limit (insurance, PCP, lease) in km, and the day its year starts.
    mileage_limit_km: float | None = Field(None, ge=0, le=200_000)
    mileage_year_start: str | None = Field(None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


def _ready(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('settings') IS NOT NULL").fetchone()[0]


def _scope(conn: psycopg.Connection, scope: str) -> dict:
    if not _ready(conn):
        return {}
    rows = conn.execute("SELECT key, value FROM settings WHERE scope = %s", (scope,)).fetchall()
    return dict(rows)


def get_all(conn: psycopg.Connection) -> dict:
    vehicles = {}
    has_cols = conn.execute(
        "SELECT count(*) = 3 FROM information_schema.columns WHERE table_name = 'vehicles'"
        " AND column_name IN ('fuel_grade', 'fuel_kind', 'tank_litres')"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT vin, fuel_grade, fuel_kind, tank_litres FROM vehicles"
        if has_cols
        else "SELECT vin, NULL, NULL, NULL FROM vehicles"
    ).fetchall()
    for vin, grade_in_use, kind, tank_seen in rows:
        chosen = _scope(conn, vin)
        vehicles[vin] = {
            "fuel_grade": chosen.get("fuel_grade"),
            "tank_litres": chosen.get("tank_litres"),
            # What the collector last used: the chosen grade or the default for the car.
            "fuel_grade_in_use": grade_in_use,
            "fuel_kind": kind,
            "tank_litres_detected": tank_seen,
            "mileage_limit_km": chosen.get("mileage_limit_km"),
            "mileage_year_start": chosen.get("mileage_year_start"),
        }
    chosen = _scope(conn, "global")
    return {
        "global": {
            **DEFAULTS,
            **chosen,
            "alerts": {**ALERT_DEFAULTS, **(chosen.get("alerts") or {})},
        },
        "vehicles": vehicles,
        "grades": GRADES,
    }


def save(conn: psycopg.Connection, scope: str, values: dict) -> None:
    if not _ready(conn):
        raise RuntimeError("settings table missing; is the collector up to date?")
    with conn.transaction():
        for key, value in values.items():
            if value is None:
                continue
            if key == "alerts":
                current = conn.execute(
                    "SELECT value FROM settings WHERE scope = %s AND key = 'alerts'", (scope,)
                ).fetchone()
                value = {
                    **(current[0] if current else {}),
                    **{k: v for k, v in value.items() if v is not None},
                }
            if key in CLEARABLE and value in (0, ""):
                conn.execute("DELETE FROM settings WHERE scope = %s AND key = %s", (scope, key))
                # Still bump updated_at somewhere so the collector notices.
                conn.execute(
                    "INSERT INTO settings (scope, key, value) VALUES (%s, '_changed', 'true')"
                    " ON CONFLICT (scope, key) DO UPDATE SET updated_at = now()",
                    (scope,),
                )
                continue
            conn.execute(
                "INSERT INTO settings (scope, key, value) VALUES (%s, %s, %s)"
                " ON CONFLICT (scope, key) DO UPDATE SET value = EXCLUDED.value,"
                " updated_at = now()",
                (scope, key, Jsonb(value)),
            )
