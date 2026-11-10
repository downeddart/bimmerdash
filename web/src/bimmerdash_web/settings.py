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
DEFAULTS = {"distance_unit": "mi", "economy_unit": "mpg"}


class GlobalSettings(BaseModel):
    distance_unit: str | None = Field(None, pattern="^(km|mi)$")
    economy_unit: str | None = Field(None, pattern="^(mpg|l100)$")


class VehicleSettings(BaseModel):
    fuel_grade: str | None = Field(None, pattern="^(E10|E5|B7_STANDARD|B7_PREMIUM)$")
    # 0 clears it (use what the car reports)
    tank_litres: float | None = Field(None, ge=0, le=200)


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
        }
    return {
        "global": {**DEFAULTS, **_scope(conn, "global")},
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
            if key == "tank_litres" and value == 0:
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
