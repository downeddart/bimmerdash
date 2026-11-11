"""Car health: service schedule, tyre pressures, warnings, fault memory and alerts."""

from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg

from .queries import latest

CBS = "vehicle.status.conditionBasedServices"
CHECK_CONTROL = "vehicle.status.checkControlMessages"
MOT_DATE = "vehicle.status.serviceTime.inspectionDateLegal"
SERVICE_DISTANCE = "vehicle.status.serviceDistance.next"
DTC = "vehicle.electronicControlUnit.diagnosticTroubleCodes.raw"
BATTERY_RECHARGE = "vehicle.electricalSystem.battery.serviceDemand.recharge"
BATTERY_REPLACE = "vehicle.electricalSystem.battery.serviceDemand.replace"
WHEELS = {
    "front_left": "vehicle.chassis.axle.row1.wheel.left.tire",
    "front_right": "vehicle.chassis.axle.row1.wheel.right.tire",
    "rear_left": "vehicle.chassis.axle.row2.wheel.left.tire",
    "rear_right": "vehicle.chassis.axle.row2.wheel.right.tire",
}
TYRE_LOW = 10  # % below target: "low"
TYRE_VERY_LOW = 20
SOON_DAYS = 60


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(v) -> str | None:
    if not isinstance(v, str):
        return None
    t = v.strip()
    return t if t and t != "-" else None


def parse_date(raw) -> date | None:
    """BMW dates: '2027-01', '2027-01-02T00:00:00Z' or '01.09.2027 00:00:00 UTC'."""
    t = _text(raw)
    if not t:
        return None
    for fmt, n in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(t[:n], fmt).date()
        except ValueError:
            continue
    return None


def _state(due: date | None, status: str, today: date) -> str:
    status = status.upper()
    if status == "OVERDUE" or (due and due < today):
        return "overdue"
    if status == "PENDING" or (due and (due - today).days <= SOON_DAYS):
        return "soon"
    return "ok"


def services(lv: dict, today: date) -> list[dict]:
    items = (lv.get(CBS) or {}).get("value")
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        due = parse_date(item.get("date"))
        status = str(item.get("status") or "")
        out.append(
            {
                "name": _text(item.get("title")) or _text(item.get("type")) or "Service",
                "type": item.get("type"),
                "due": due,
                # BMW's own distance remaining, in km.
                "distance_km": _num(item.get("unitOfLengthRemaining")),
                "state": _state(due, status, today),
                "description": _text(item.get("description")),
            }
        )
    mot = parse_date((lv.get(MOT_DATE) or {}).get("value"))
    if mot and not any("inspection" in s["name"].lower() or s["name"] == "MOT" for s in out):
        out.append(
            {
                "name": "MOT",
                "type": "MOT",
                "due": mot,
                "distance_km": None,
                "state": _state(mot, "", today),
                "description": None,
            }
        )
    for s in out:
        if "statutory" in s["name"].lower() or "inspection" in s["name"].lower():
            s["name"] = "MOT"
    rank = {"overdue": 0, "soon": 1, "ok": 2}
    return sorted(out, key=lambda s: (rank[s["state"]], s["due"] or date.max))


def tyres(lv: dict) -> dict | None:
    wheels = {}
    for pos, base in WHEELS.items():
        p = lv.get(base + ".pressure")
        target = _num((lv.get(base + ".pressureTarget") or {}).get("value"))
        temp = _num((lv.get(base + ".temperature") or {}).get("value"))
        pressure = _num(p["value"]) if p else None
        if pressure is None:
            continue
        short = (target - pressure) / target * 100 if target else None
        wheels[pos] = {
            "kpa": pressure,
            "target_kpa": target,
            "temp_c": temp,
            "below_target_pct": round(short, 1) if short is not None else None,
            "state": (
                "unknown"
                if short is None
                else "very_low"
                if short >= TYRE_VERY_LOW
                else "low"
                if short >= TYRE_LOW
                else "ok"
            ),
            "time": p["time"],
        }
    if not wheels:
        return None
    states = [w["state"] for w in wheels.values()]
    worst = next((s for s in ("very_low", "low", "ok") if s in states), "unknown")
    return {"wheels": wheels, "state": worst, "time": max(w["time"] for w in wheels.values())}


def warnings(lv: dict) -> dict | None:
    e = lv.get(CHECK_CONTROL)
    if e is None:
        return None
    items = e["value"] if isinstance(e["value"], list) else []
    out = []
    for item in items:
        if isinstance(item, dict):
            text = next(
                (
                    _text(str(item[k]))
                    for k in ("title", "text", "name", "type", "description")
                    if item.get(k) and _text(str(item[k]))
                ),
                "Check Control message",
            )
            out.append({"text": text, "severity": item.get("severity"), "raw": item})
        else:
            out.append({"text": str(item), "severity": None, "raw": item})
    return {"items": out, "time": e["time"]}


def fault_memory(lv: dict) -> dict | None:
    """The raw fault memory: 'ecu,code;ecu,code;'. Mostly historic entries; counts only."""
    e = lv.get(DTC)
    if e is None or not isinstance(e["value"], str):
        return None
    codes = [c.strip() for c in e["value"].split(";") if c.strip()]
    ecus = sorted({c.split(",", 1)[0] for c in codes if "," in c})
    return {"count": len(codes), "modules": len(ecus), "codes": codes, "time": e["time"]}


def battery(lv: dict) -> dict | None:
    rec, rep = lv.get(BATTERY_RECHARGE), lv.get(BATTERY_REPLACE)
    if rec is None and rep is None:
        return None
    return {
        "recharge": (rec or {}).get("value"),
        "replace": (rep or {}).get("value"),
        "time": max(x["time"] for x in (rec, rep) if x),
    }


def alerts(conn: psycopg.Connection, vin: str, limit: int = 20) -> dict:
    if not conn.execute("SELECT to_regclass('alerts') IS NOT NULL").fetchone()[0]:
        return {"open": [], "recent": [], "pushover": None}
    rows = conn.execute(
        "SELECT id, kind, ref, title, message, raised_at, sent_at, send_error, cleared_at"
        " FROM alerts WHERE vin = %s ORDER BY raised_at DESC LIMIT %s",
        (vin, limit),
    ).fetchall()
    cols = (
        "id",
        "kind",
        "ref",
        "title",
        "message",
        "raised_at",
        "sent_at",
        "send_error",
        "cleared_at",
    )
    items = [dict(zip(cols, r, strict=True)) for r in rows]
    channel = conn.execute(
        "SELECT value FROM collector_state WHERE key = 'alerts_channel'"
    ).fetchone()
    return {
        "open": [a for a in items if a["cleared_at"] is None],
        "recent": items,
        "pushover": channel[0].get("pushover") if channel else None,
    }


def rest_status(conn: psycopg.Connection, vin: str) -> dict:
    row = conn.execute(
        "SELECT max(received_at) FROM raw_messages WHERE vin = %s AND topic LIKE 'rest/%%'"
        " AND received_at > now() - interval '30 days'",
        (vin,),
    ).fetchone()
    return {"last_poll": row[0]}


def health(conn: psycopg.Connection, vin: str, today: date | None = None) -> dict:
    today = today or datetime.now(UTC).date()
    lv = latest(conn, vin)
    svc = services(lv, today)
    sd = lv.get(SERVICE_DISTANCE)
    tyre = tyres(lv)
    warn = warnings(lv)
    al = alerts(conn, vin)
    issues = (
        sum(1 for s in svc if s["state"] == "overdue")
        + (1 if tyre and tyre["state"] in ("low", "very_low") else 0)
        + (len(warn["items"]) if warn else 0)
    )
    return {
        "issues": issues,
        "due_soon": sum(1 for s in svc if s["state"] == "soon"),
        "services": svc,
        "service_distance": {"value": _num(sd["value"]), "unit": sd["unit"]} if sd else None,
        "tyres": tyre,
        "warnings": warn,
        "fault_memory": fault_memory(lv),
        "battery": battery(lv),
        "alerts": al,
        "rest": rest_status(conn, vin),
    }
