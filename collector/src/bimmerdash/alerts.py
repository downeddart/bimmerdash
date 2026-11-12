"""Alerts: things worth a push notification, worked out from the latest values.

Each rule returns the alerts that are true *now*. An alert is stored (and pushed) when it
first becomes true, stays open while it remains true, and is cleared when it stops being
true, so the same thing is never sent twice in a row. Rules have some hysteresis (e.g.
fuel must rise 5 points above the threshold) so a value hovering at the limit doesn't
flap.

Pushover is optional: without it, alerts are still listed in the web UI.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

PSI_PER_KPA = 0.145038
KM_PER_MILE = 1.609344
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

DEFAULTS = {
    "enabled": True,
    "left_open": True,
    "left_open_minutes": 10,
    "low_fuel": True,
    "low_fuel_percent": 20,
    "tyres": True,
    "tyre_low_percent": 10,  # below the car's own target pressure
    "service": True,
    "service_days": 30,
    "warnings": True,
}

DOORS = "vehicle.cabin.door.status"
OPENABLE = {
    "vehicle.cabin.door.row1.driver.isOpen": "driver's door",
    "vehicle.cabin.door.row1.passenger.isOpen": "front passenger door",
    "vehicle.cabin.door.row2.driver.isOpen": "rear left door",
    "vehicle.cabin.door.row2.passenger.isOpen": "rear right door",
    "vehicle.body.trunk.isOpen": "boot",
    "vehicle.body.trunk.door.isOpen": "boot",
    "vehicle.body.trunk.window.isOpen": "rear window",
    "vehicle.body.hood.isOpen": "bonnet",
}
WINDOWS = {
    "vehicle.cabin.window.row1.driver.status": "driver's window",
    "vehicle.cabin.window.row1.passenger.status": "front passenger window",
    "vehicle.cabin.window.row2.driver.status": "rear left window",
    "vehicle.cabin.window.row2.passenger.status": "rear right window",
}
SUNROOF = "vehicle.cabin.sunroof.overallStatus"
FUEL_PERCENT = "vehicle.drivetrain.fuelSystem.level"
FUEL_LITRES = "vehicle.drivetrain.fuelSystem.remainingFuel"
RANGE = "vehicle.drivetrain.lastRemainingRange"
LATITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LONGITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
CBS = "vehicle.status.conditionBasedServices"
CHECK_CONTROL = "vehicle.status.checkControlMessages"
MOT_DATE = "vehicle.status.serviceTime.inspectionDateLegal"
WHEELS = {
    "front left": "vehicle.chassis.axle.row1.wheel.left.tire",
    "front right": "vehicle.chassis.axle.row1.wheel.right.tire",
    "rear left": "vehicle.chassis.axle.row2.wheel.left.tire",
    "rear right": "vehicle.chassis.axle.row2.wheel.right.tire",
}


@dataclass(frozen=True)
class Alert:
    kind: str
    ref: str
    title: str
    message: str
    priority: int = 0


Latest = dict[str, dict]  # key -> {value, unit, time}


def _v(lv: Latest, key: str):
    e = lv.get(key)
    return None if e is None else e["value"]


def _num(lv: Latest, key: str) -> float | None:
    v = _v(lv, key)
    try:
        f = None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None
    # "inf"/"nan" strings parse as floats but are not readings.
    return f if f is not None and math.isfinite(f) else None


def _is_open(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).upper() in ("TRUE", "OPEN", "INTERMEDIATE", "TILT", "OPEN_TILT")


# --- rules ---------------------------------------------------------------------------


def left_open(
    lv: Latest, now: datetime, minutes: float, quiet_since: datetime | None
) -> list[Alert]:
    """Unlocked, or something open, once the car has gone quiet (parked, nobody at it).

    quiet_since is when the car last sent anything over the stream: it sends whenever a
    door, lock or window changes, so silence means it's been left as it is.
    """
    if not lv or quiet_since is None:
        return []
    if now - quiet_since < timedelta(minutes=minutes):
        return []
    problems = []
    doors = _v(lv, DOORS)
    if doors is not None and str(doors).upper() not in ("LOCKED", "SECURED"):
        problems.append("unlocked")
    seen = set()
    for key, label in {**OPENABLE, **WINDOWS}.items():
        v = _v(lv, key)
        if v is None or label in seen:
            continue
        if (key in WINDOWS and str(v).upper() != "CLOSED") or (key in OPENABLE and _is_open(v)):
            problems.append(f"{label} open")
            seen.add(label)
    sunroof = _v(lv, SUNROOF)
    if sunroof is not None and str(sunroof).upper() not in ("CLOSED", "UNKNOWN"):
        problems.append("sunroof open")
    if not problems:
        return []
    what = ", ".join(problems)
    return [
        Alert(
            "left_open",
            "",
            "Car left open" if "unlocked" not in problems else "Car left unlocked",
            f"Parked for {round((now - quiet_since).total_seconds() / 60)} min: {what}.",
            priority=1,
        )
    ]


def low_fuel(lv: Latest, threshold: float, was_open: bool, cheapest=None) -> list[Alert]:
    pct = _num(lv, FUEL_PERCENT)
    if pct is None:
        return []
    # Hysteresis: once raised, stays until 5 points above the threshold.
    if pct > threshold + (5 if was_open else 0):
        return []
    litres = _num(lv, FUEL_LITRES)
    rng = _num(lv, RANGE)
    if rng is not None and str((lv.get(RANGE) or {}).get("unit") or "km").lower() in (
        "mi",
        "miles",
    ):
        rng *= KM_PER_MILE
    bits = [f"{pct:.0f}% left"]
    if litres is not None:
        bits[0] += f" ({litres:.0f} L)"
    if rng is not None:
        bits.append(f"range about {rng / KM_PER_MILE:.0f} mi")
    msg = ", ".join(bits) + "."
    if cheapest:
        msg += f" Cheapest nearby: {cheapest['name']}, {cheapest['price']:.1f}p"
        msg += f" ({cheapest['km'] / KM_PER_MILE:.1f} mi away)."
    return [Alert("low_fuel", "", "Fuel low", msg)]


def tyres(lv: Latest, low_percent: float, open_refs: set[str]) -> list[Alert]:
    out = []
    for name, base in WHEELS.items():
        p, target = _num(lv, base + ".pressure"), _num(lv, base + ".pressureTarget")
        if p is None or not target:
            continue
        short = (target - p) / target * 100
        # Raise at low_percent below target; clear only once back within 5%.
        limit = 5 if name in open_refs else low_percent
        if short >= limit:
            out.append(
                Alert(
                    "tyre",
                    name,
                    "Tyre pressure low",
                    f"{name.capitalize()} tyre is {p * PSI_PER_KPA:.0f} psi, {short:.0f}% below"
                    f" the car's target of {target * PSI_PER_KPA:.0f} psi. Pump it up when"
                    " the tyres are cold.",
                    priority=1 if short >= 20 else 0,
                )
            )
    return out


def _due(raw) -> date | None:
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "-":
        return None
    text = raw.strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%d.%m.%Y", 10)):
        try:
            return datetime.strptime(text[:n], fmt).date()
        except ValueError:
            continue
    return None


def service(lv: Latest, today: date, days: int) -> list[Alert]:
    out = []
    items = _v(lv, CBS)
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or item.get("type") or "Service").strip()
        status = str(item.get("status") or "").upper()
        due = _due(item.get("date"))
        overdue = status == "OVERDUE" or (due is not None and due < today)
        soon = status == "PENDING" or (due is not None and due <= today + timedelta(days=days))
        if overdue or soon:
            when = f" by {due:%b %Y}" if due else ""
            out.append(
                Alert(
                    "service",
                    name.lower(),
                    f"{name} {'overdue' if overdue else 'due soon'}",
                    f"{name} is {'overdue' if overdue else 'due'}{when}.",
                    priority=1 if overdue else 0,
                )
            )
    mot = _due(_v(lv, MOT_DATE))
    if mot and mot <= today + timedelta(days=days):
        overdue = mot < today
        out.append(
            Alert(
                "service",
                "mot",
                "MOT overdue" if overdue else "MOT due soon",
                f"MOT {'was' if overdue else 'is'} due on {mot:%-d %b %Y}.",
                priority=1 if overdue else 0,
            )
        )
    return out


def warnings(lv: Latest) -> list[Alert]:
    items = _v(lv, CHECK_CONTROL)
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            text = next(
                (
                    str(item[k]).strip()
                    for k in ("title", "text", "name", "type", "description")
                    if item.get(k) and str(item[k]).strip() not in ("", "-")
                ),
                "Check Control message",
            )
            ref = str(item.get("id") or item.get("type") or text)
            severity = str(item.get("severity") or "").upper()
        else:
            text = ref = str(item)
            severity = ""
        out.append(
            Alert(
                "warning",
                ref[:120],
                "Warning from your car",
                text,
                priority=1 if severity in ("HIGH", "RED", "ERROR") else 0,
            )
        )
    return out


# --- running the rules ---------------------------------------------------------------


def settings(conn) -> dict:
    row = conn.execute(
        "SELECT value FROM settings WHERE scope = 'global' AND key = 'alerts'"
    ).fetchone()
    return {**DEFAULTS, **(row[0] if row and isinstance(row[0], dict) else {})}


def _latest(conn, vin: str) -> Latest:
    rows = conn.execute(
        "SELECT key, value, unit, time FROM latest_values WHERE vin = %s", (vin,)
    ).fetchall()
    return {k: {"value": v, "unit": u, "time": t} for k, v, u, t in rows}


def cheapest_near(conn, lat: float, lon: float, grade: str, radius_km: float) -> dict | None:
    dlat = radius_km / 111.32
    dlon = dlat / max(0.2, abs(math.cos(math.radians(lat))))
    row = conn.execute(
        """
        WITH near AS (
            SELECT node_id, trading_name,
                   6371 * sqrt(power(radians(latitude - %(lat)s), 2)
                     + power(radians(longitude - %(lon)s) * cos(radians(%(lat)s)), 2)) AS km
            FROM forecourts
            WHERE NOT closed
              AND latitude BETWEEN %(lat)s - %(dlat)s AND %(lat)s + %(dlat)s
              AND longitude BETWEEN %(lon)s - %(dlon)s AND %(lon)s + %(dlon)s
        )
        SELECT n.trading_name, p.price, n.km FROM near n
        CROSS JOIN LATERAL (
            SELECT price, effective_at FROM fuel_prices
            WHERE node_id = n.node_id AND fuel_type = %(grade)s
            ORDER BY effective_at DESC LIMIT 1
        ) p
        WHERE n.km <= %(r)s AND p.price BETWEEN 80 AND 260
          AND p.effective_at > now() - interval '30 days'
        ORDER BY p.price, n.km LIMIT 1
        """,
        {"lat": lat, "lon": lon, "dlat": dlat, "dlon": dlon, "grade": grade, "r": radius_km},
    ).fetchone()
    if not row:
        return None
    name = (row[0] or "A station").strip()
    if name.isupper():
        name = name.title()
    return {"name": name, "price": row[1], "km": row[2]}


def evaluate(conn, vin: str, cfg: dict, open_refs: dict[str, set[str]], now: datetime):
    lv = _latest(conn, vin)
    found: list[Alert] = []
    if cfg["left_open"]:
        quiet_since = conn.execute(
            "SELECT max(received_at) FROM raw_messages WHERE vin = %s"
            " AND received_at > %s::timestamptz - interval '7 days' AND topic NOT LIKE 'rest/%%'",
            (vin, now),
        ).fetchone()[0]
        found += left_open(lv, now, cfg["left_open_minutes"], quiet_since)
    if cfg["low_fuel"]:
        cheapest = None
        lat, lon = _num(lv, LATITUDE), _num(lv, LONGITUDE)
        pct = _num(lv, FUEL_PERCENT)
        near_threshold = pct is not None and pct <= cfg["low_fuel_percent"] + 5
        located = lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180
        if near_threshold and located:
            grade = conn.execute(
                "SELECT COALESCE(fuel_grade, 'E10') FROM vehicles WHERE vin = %s", (vin,)
            ).fetchone()
            cheapest = cheapest_near(conn, lat, lon, grade[0] if grade else "E10", 8)
        found += low_fuel(lv, cfg["low_fuel_percent"], bool(open_refs.get("low_fuel")), cheapest)
    if cfg["tyres"]:
        found += tyres(lv, cfg["tyre_low_percent"], open_refs.get("tyre", set()))
    if cfg["service"]:
        found += service(lv, now.date(), cfg["service_days"])
    if cfg["warnings"]:
        found += warnings(lv)
    return found


class Notifier:
    def __init__(self, http: httpx.Client, token: str | None, user: str | None, url: str | None):
        self.http = http
        self.token = token
        self.user = user
        self.url = url

    @classmethod
    def from_env(cls, http: httpx.Client) -> Notifier:
        return cls(
            http,
            os.environ.get("BIMMERDASH_PUSHOVER_TOKEN") or None,
            os.environ.get("BIMMERDASH_PUSHOVER_USER") or None,
            os.environ.get("BIMMERDASH_PUBLIC_URL") or None,
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.user)

    def send(self, alert: Alert) -> None:
        body = {
            "token": self.token,
            "user": self.user,
            "title": alert.title,
            "message": alert.message,
            "priority": alert.priority,
        }
        if self.url:
            body |= {"url": self.url, "url_title": "Open bimmerdash"}
        r = self.http.post(PUSHOVER_URL, data=body, timeout=15)
        r.raise_for_status()


def save_channel(db, notifier: Notifier) -> None:
    """Tell the web UI whether alerts are pushed (it can't see the collector's .env)."""
    db.conn.execute(
        "INSERT INTO collector_state (key, value) VALUES ('alerts_channel', %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (Jsonb({"pushover": notifier.configured}),),
    )
    db.conn.commit()


def run(db, notifier: Notifier, now: datetime | None = None) -> int:
    """Evaluate every car, store new alerts, clear finished ones, push new ones.

    Returns how many new alerts were raised.
    """
    now = now or datetime.now(UTC)
    conn = db.conn
    cfg = settings(conn)
    if not cfg["enabled"]:
        conn.commit()
        return 0
    raised = 0
    for (vin,) in conn.execute("SELECT vin FROM vehicles").fetchall():
        # One car's bad data must not stop alerts for the others.
        try:
            raised += _run_vin(conn, notifier, vin, cfg, now)
        except Exception as e:
            conn.rollback()
            log.error("alert check failed for %s…: %s", vin[:5], e.__class__.__name__)
    return raised


def _run_vin(conn, notifier: Notifier, vin: str, cfg: dict, now: datetime) -> int:
    """Evaluate one car: store new alerts, clear finished ones, push new ones."""
    raised = 0
    open_rows = conn.execute(
        "SELECT id, kind, ref FROM alerts WHERE vin = %s AND cleared_at IS NULL", (vin,)
    ).fetchall()
    open_refs: dict[str, set[str]] = {}
    for _, kind, ref in open_rows:
        open_refs.setdefault(kind, set()).add(ref)
    current = evaluate(conn, vin, cfg, open_refs, now)
    active = {(a.kind, a.ref) for a in current}
    for alert_id, kind, ref in open_rows:
        if (kind, ref) not in active:
            conn.execute("UPDATE alerts SET cleared_at = %s WHERE id = %s", (now, alert_id))
    new, seen = [], set()
    for a in current:
        # Two warnings can map to one ref; a second insert would break the open-alert index.
        if a.ref in open_refs.get(a.kind, set()) or (a.kind, a.ref) in seen:
            continue
        seen.add((a.kind, a.ref))
        new.append(a)
    for a in new:
        alert_id = conn.execute(
            "INSERT INTO alerts (vin, kind, ref, title, message, raised_at)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (vin, a.kind, a.ref, a.title, a.message, now),
        ).fetchone()[0]
        conn.commit()
        raised += 1
        if not notifier.configured:
            continue
        try:
            notifier.send(a)
            conn.execute("UPDATE alerts SET sent_at = now() WHERE id = %s", (alert_id,))
        except httpx.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            err = f"HTTP {status}" if status else e.__class__.__name__
            log.warning("Pushover send failed: %s", err)
            conn.execute("UPDATE alerts SET send_error = %s WHERE id = %s", (err, alert_id))
    conn.commit()
    return raised
