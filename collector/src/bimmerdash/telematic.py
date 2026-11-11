"""Poll the CarData REST API for what the stream doesn't carry.

Some keys never arrive over MQTT: the service schedule (condition based services),
Check Control warnings and the car's lifetime fuel counters. Others (tyre pressures,
fault memory) arrive rarely. A *container* is BMW's saved list of keys; one GET of
``telematicData`` returns the latest value of each, at one request per car.

Responses are stored exactly like stream messages (raw JSONL, then telemetry), with
topic ``rest/telematicData``, so everything downstream reads one table.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from psycopg.types.json import Jsonb

from .cardata_api import CarDataApi, QuotaExceeded

log = logging.getLogger(__name__)

CONTAINER_NAME = "bimmerdash"
CONTAINER_PURPOSE = "bimmerdash: service, tyres, warnings and fuel use"
STATE_KEY = "telematic_container"
TOPIC = "rest/telematicData"

CBS = "vehicle.status.conditionBasedServices"
CHECK_CONTROL = "vehicle.status.checkControlMessages"
LIFETIME_FUEL = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel"
LIFETIME_DISTANCE = (
    "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.referenceDistance"
)

DESCRIPTORS = [
    # REST only
    CBS,
    CHECK_CONTROL,
    LIFETIME_FUEL,
    LIFETIME_DISTANCE,
    "vehicle.status.conditionBasedServicesAverageDistancePerDay",
    "vehicle.electricalSystem.battery.serviceDemand.recharge",
    "vehicle.electricalSystem.battery.serviceDemand.replace",
    "vehicle.serviceDemand.defect.id",
    # Streamed, but rarely: ask so they're never stale for long
    "vehicle.status.serviceDistance.next",
    "vehicle.status.serviceTime.inspectionDateLegal",
    "vehicle.electronicControlUnit.diagnosticTroubleCodes.raw",
    *(
        f"vehicle.chassis.axle.row{row}.wheel.{side}.tire.{what}"
        for row in (1, 2)
        for side in ("left", "right")
        for what in ("pressure", "pressureTarget", "temperature")
    ),
    "vehicle.vehicle.travelledDistance",
    "vehicle.drivetrain.fuelSystem.level",
    "vehicle.drivetrain.fuelSystem.remainingFuel",
    "vehicle.drivetrain.lastRemainingRange",
]
# BMW sends a cleared list as null rather than []; for these, null means "none".
EMPTY_WHEN_NULL = {CHECK_CONTROL}


def signature(descriptors: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(set(descriptors))).encode()).hexdigest()[:12]


def parse_value(raw):
    """BMW sends every value as text. Numbers become numbers; JSON text becomes JSON."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if text[:1] in ("[", "{"):
        try:
            return json.loads(text)
        except ValueError:
            return raw
    try:
        n = float(text)
    except ValueError:
        return raw
    return int(n) if n.is_integer() and "." not in text and "e" not in text.lower() else n


def to_record(vin: str, data: dict, now: datetime) -> dict:
    """A telematicData response as a stored record, the same shape as an MQTT message."""
    points = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if value is None or value == "":
            if key not in EMPTY_WHEN_NULL:
                continue  # the car hasn't reported this one
            value = []
        else:
            value = parse_value(value)
        ts = entry.get("timestamp") or now.isoformat()
        points[key] = {"value": value, "unit": entry.get("unit"), "timestamp": ts}
    return {
        "received_at": now.isoformat(),
        "topic": f"{TOPIC}/{vin}",
        # polled_at makes every poll its own raw message, which is how last_poll() knows
        # when the car was last asked even when nothing changed.
        "payload": {"vin": vin, "polled_at": now.isoformat(), "data": points},
    }


class TelematicPoller:
    """Polls each car every `every`, creating the container on first use."""

    def __init__(self, api: CarDataApi, db, writer, every: timedelta):
        self.api = api
        self.db = db
        self.writer = writer
        self.every = every
        self._next_try: datetime | None = None

    # --- container id, kept in collector_state --------------------------------------

    def _state(self) -> dict | None:
        row = self.db.conn.execute(
            "SELECT value FROM collector_state WHERE key = %s", (STATE_KEY,)
        ).fetchone()
        self.db.conn.commit()
        return row[0] if row else None

    def _save_state(self, value: dict | None) -> None:
        if value is None:
            self.db.conn.execute("DELETE FROM collector_state WHERE key = %s", (STATE_KEY,))
        else:
            self.db.conn.execute(
                "INSERT INTO collector_state (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (STATE_KEY, Jsonb(value)),
            )
        self.db.conn.commit()

    def container_id(self) -> str:
        sig = signature(DESCRIPTORS)
        state = self._state()
        if state and state.get("signature") == sig and state.get("id"):
            return state["id"]
        cid = self.api.create_container(CONTAINER_NAME, CONTAINER_PURPOSE, DESCRIPTORS)
        self._save_state({"id": cid, "signature": sig})
        log.info("created CarData container for %d keys", len(DESCRIPTORS))
        return cid

    # --- polling --------------------------------------------------------------------

    def last_poll(self, vin: str) -> datetime | None:
        row = self.db.conn.execute(
            "SELECT max(received_at) FROM raw_messages WHERE vin = %s AND topic LIKE %s",
            (vin, TOPIC + "/%"),
        ).fetchone()
        self.db.conn.commit()
        return row[0]

    def due(self, vin: str, now: datetime) -> bool:
        if self._next_try and now < self._next_try:
            return False
        last = self.last_poll(vin)
        return last is None or now - last >= self.every

    def poll(self, vin: str, now: datetime | None = None) -> int:
        """Fetch and store one car's values. Returns how many values BMW sent."""
        now = now or datetime.now(UTC)
        cid = self.container_id()
        try:
            data = self.api.telematic_data(vin, cid)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 403, 404):
                # Container deleted in the portal, or BMW rejected it: make a new one next time.
                self._save_state(None)
            raise
        record = to_record(vin, data, now)
        if self.writer is not None:
            self.writer.write(record["topic"], json.dumps(record["payload"]).encode(), now=now)
        return self.db.ingest(record)

    def run_due(self, vins: list[str], now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        for vin in vins:
            if not self.due(vin, now):
                continue
            try:
                n = self.poll(vin, now)
                log.info("CarData REST: %d value(s) received", n)
            except QuotaExceeded as e:
                log.warning("CarData REST skipped: %s", e)
                self._next_try = now + timedelta(hours=1)
                return
            except (httpx.HTTPError, ValueError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                log.warning(
                    "CarData REST poll failed: %s",
                    f"HTTP {status}" if status else e.__class__.__name__,
                )
                self._next_try = now + timedelta(minutes=30)
                return
