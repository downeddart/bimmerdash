"""Receipts: what was actually paid for a fill-up, entered by hand.

The collector rebuilds the fillups table from scratch, so receipts live in their own table
and are matched to fill-ups when they are read. A rebuild can move a fill-up's time a
little (a later position fix can pin it to the forecourt), so a receipt belongs to the
nearest fill-up within MATCH_WINDOW of the time it was entered against.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
from pydantic import AwareDatetime, BaseModel, Field

MATCH_WINDOW = timedelta(hours=6)


class Receipt(BaseModel):
    time: AwareDatetime  # the fill-up's time, as listed
    cost_gbp: float = Field(gt=0, le=500)
    litres: float | None = Field(None, gt=0, le=150)


def _ready(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('fillup_receipts') IS NOT NULL").fetchone()[0]


def load(conn: psycopg.Connection, vin: str) -> list[dict]:
    if not _ready(conn):
        return []
    rows = conn.execute(
        "SELECT fillup_time, cost_gbp, litres FROM fillup_receipts WHERE vin = %s"
        " ORDER BY fillup_time",
        (vin,),
    ).fetchall()
    return [{"time": t, "cost_gbp": c, "litres": lt} for t, c, lt in rows]


def apply(fills: list[dict], receipts: list[dict]) -> None:
    """Put each receipt's figures on its fill-up (fills as read from the fillups table).

    Litres come from the receipt if given; otherwise, when the fill-up was priced at the
    station's own price, the receipt total divided by that price is far closer than the
    car's whole-litre readings. The car's figure stays in litres_sensor.
    """
    unused = list(receipts)
    for f in fills:
        f["receipt"] = None
        f["litres_sensor"] = f["litres_added"]
        near = [r for r in unused if abs(r["time"] - f["time"]) <= MATCH_WINDOW]
        if not near:
            continue
        r = min(near, key=lambda r: abs(r["time"] - f["time"]))
        unused.remove(r)
        cost = round(r["cost_gbp"], 2)
        if r["litres"]:
            litres, ppl = r["litres"], cost * 100 / r["litres"]
        elif f["price_source"] == "station" and f["price_ppl"]:
            litres, ppl = cost * 100 / f["price_ppl"], f["price_ppl"]
        elif f["litres_added"]:
            litres, ppl = f["litres_added"], cost * 100 / f["litres_added"]
        else:
            litres, ppl = None, None
        f["receipt"] = {"cost_gbp": cost, "litres": r["litres"]}
        f["cost_gbp"] = cost
        f["price_source"] = "receipt"
        f["price_ppl"] = round(ppl, 1) if ppl else None
        if litres is not None:
            f["litres_added"] = round(litres, 2)
            f["litres_estimated"] = False


def _fillup_near(conn: psycopg.Connection, vin: str, time) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM fillups WHERE vin = %s AND time BETWEEN %s - %s AND %s + %s",
            (vin, time, MATCH_WINDOW, time, MATCH_WINDOW),
        ).fetchone()
        is not None
    )


def _forget_near(conn: psycopg.Connection, vin: str, time) -> int:
    # Also clears a receipt entered before a rebuild moved the fill-up's time.
    return conn.execute(
        "DELETE FROM fillup_receipts WHERE vin = %s AND fillup_time BETWEEN %s - %s AND %s + %s",
        (vin, time, MATCH_WINDOW, time, MATCH_WINDOW),
    ).rowcount


def save(conn: psycopg.Connection, vin: str, r: Receipt) -> bool:
    """Store a receipt against the fill-up at r.time. False if there is no such fill-up."""
    if not _ready(conn) or not _fillup_near(conn, vin, r.time):
        return False
    with conn.transaction():
        _forget_near(conn, vin, r.time)
        conn.execute(
            "INSERT INTO fillup_receipts (vin, fillup_time, cost_gbp, litres)"
            " VALUES (%s, %s, %s, %s)",
            (vin, r.time, round(r.cost_gbp, 2), r.litres),
        )
    return True


def delete(conn: psycopg.Connection, vin: str, time) -> bool:
    if not _ready(conn):
        return False
    with conn.transaction():
        return _forget_near(conn, vin, time) > 0
