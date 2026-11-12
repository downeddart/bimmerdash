"""Subscribe to the BMW CarData MQTT stream and store every message.

Each message is appended untouched to a daily JSONL file first (the safety net), then written
to the database. If the database is unavailable nothing is lost: `bimmerdash import-raw`
replays the JSONL files.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

from . import alerts
from .auth import AuthError, TokenStore, refresh
from .cardata_api import CarDataApi, fetch_vehicle_details, normalise_image
from .config import Config
from .db import Database
from .derive import rebuild_all, settings_changed_at
from .telematic import TelematicPoller

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 30
SUMMARY_EVERY = 600  # seconds between "received N messages" log lines
DETAILS_MAX_AGE = timedelta(days=30)  # refresh vehicle basic data + image monthly
DETAILS_RETRY = 6 * 3600  # after a failed fetch, wait before trying that VIN again
DERIVE_AFTER_DATA = 300  # rebuild trips/fill-ups this long after new data arrives
DERIVE_EVERY = 6 * 3600  # and at least this often (prices and averages change too)
CHECK_EVERY = 60  # seconds between REST-poll and alert checks


class RawWriter:
    """Append one JSON line per message to <dir>/YYYY-MM-DD.jsonl (UTC)."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Raw data holds the VIN and GPS history: owner-only, also for files and folders
        # created by older versions.
        for d in (self.directory.parent, self.directory):
            _chmod(d, 0o700)
        for f in self.directory.glob("*.jsonl"):
            _chmod(f, 0o600)
        self._lock = threading.Lock()

    def write(self, topic: str, payload: bytes, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            body = payload.decode("utf-8", errors="replace")
        record = {"received_at": now.isoformat(), "topic": topic, "payload": body}
        path = self.directory / f"{now:%Y-%m-%d}.jsonl"
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self._lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(line)
        return record


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as e:  # not ours (e.g. a bind mount); leave it
        log.debug("could not chmod %s: %s", path, e)


def run_stream(cfg: Config) -> int:
    store = TokenStore(cfg.token_path)
    tokens = store.load()
    if tokens is None:
        log.error("no tokens found at %s; run `bimmerdash login` first", cfg.token_path)
        return 1

    http = httpx.Client(timeout=15)
    writer = RawWriter(cfg.raw_dir)
    stop = threading.Event()
    force_refresh = threading.Event()
    topic = f"{tokens.gcid}/{cfg.vin}" if cfg.vin else f"{tokens.gcid}/#"

    def on_connect(client, _userdata, _flags, reason_code, _props):
        if reason_code.is_failure:
            log.warning("MQTT connect refused: %s", reason_code)
            if reason_code.value in (0x86, 0x87, 4, 5):  # bad credentials / not authorised
                force_refresh.set()
            return
        log.info("connected to %s, subscribing to %s", cfg.mqtt_host, _redact_topic(topic))
        client.subscribe(topic, qos=1)

    def on_disconnect(_client, _userdata, _flags, reason_code, props):
        if not stop.is_set():
            reason = getattr(props, "ReasonString", None) if props else None
            log.warning(
                "MQTT disconnected: %s%s (will reconnect)",
                reason_code,
                f" ({reason})" if reason else "",
            )

    stats = {"messages": 0, "points": 0, "db_errors": 0}
    new_points = threading.Event()  # set by the MQTT thread, cleared by derive
    ingest_db = Database(cfg.database_url) if cfg.database_url else None

    def on_message(_client, _userdata, msg):
        record = writer.write(msg.topic, msg.payload)
        stats["messages"] += 1
        if ingest_db is None:
            return
        try:
            n = ingest_db.ingest(record)
            stats["points"] += n
            if n:
                new_points.set()
        except Exception as e:  # keep streaming; JSONL still has the message
            stats["db_errors"] += 1
            log.error("database write failed (%s); message kept in JSONL", e.__class__.__name__)
            try:
                ingest_db.conn.rollback()
            except Exception:
                ingest_db._conn = None

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv5,
    )
    client.tls_set()
    client.username_pw_set(tokens.gcid, tokens.id_token)
    client.reconnect_delay_set(min_delay=2, max_delay=120)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    def handle_signal(*_):
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if tokens.expires_soon():
        force_refresh.set()

    main_db = Database(cfg.database_url) if cfg.database_url else None
    if main_db is not None:
        main_db.migrate()
        if n := main_db.recompress_images(normalise_image):
            log.info("re-encoded %d stored car image(s) as WebP", n)
    api = (
        CarDataApi(http, lambda: tokens.access_token, main_db, cfg.api_daily_budget)
        if main_db is not None
        else None
    )
    poller = (
        TelematicPoller(api, main_db, writer, timedelta(hours=cfg.rest_poll_hours))
        if api is not None and cfg.rest_poll_hours > 0
        else None
    )
    notifier = alerts.Notifier.from_env(http)
    if main_db is not None:
        log.info(
            "alerts: %s",
            "sent with Pushover" if notifier.configured else "shown in the web UI (no Pushover)",
        )
        try:
            alerts.save_channel(main_db, notifier)
        except Exception as e:
            log.warning("could not record the alert channel: %s", e.__class__.__name__)
            main_db._conn = None
    last_checks = 0.0
    failed_at: dict[str, float] = {}
    last_summary = time.monotonic()
    last_details_check = 0.0  # check soon after start
    last_derive = None  # rebuild soon after start
    data_seen_at = None
    settings_seen = None

    def maybe_fetch_details() -> None:
        """Basic data + image for new VINs, refreshed monthly. Runs on this (main) thread."""
        for vin in main_db.vehicles_needing_details(DETAILS_MAX_AGE):
            if time.monotonic() - failed_at.get(vin, -DETAILS_RETRY) < DETAILS_RETRY:
                continue
            if tokens.expires_soon(margin=120):
                return  # wait for the refresh below
            try:
                fetch_vehicle_details(api, main_db, vin)
            except Exception as e:  # back off this car whatever went wrong, then try the next
                failed_at[vin] = time.monotonic()
                log.warning("could not fetch vehicle details: %s", e)

    # BMW's broker drops connections idle for 60 s, so ping well inside that.
    client.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=KEEPALIVE_SECONDS)
    client.loop_start()
    exit_code = 0
    try:
        while not stop.is_set():
            if force_refresh.is_set() or tokens.expires_soon():
                try:
                    tokens = refresh(cfg.client_id, tokens, http)
                    store.save(tokens)
                    # paho reads these on every (re)connect, so the next reconnect uses them.
                    client.username_pw_set(tokens.gcid, tokens.id_token)
                    force_refresh.clear()
                    log.info("tokens refreshed")
                except (AuthError, httpx.HTTPError) as e:
                    log.error("%s; retrying in 60s (run `bimmerdash login` if this persists)", e)
                    stop.wait(60)
                    continue
            now = time.monotonic()
            if api is not None and now - last_details_check > 60:
                last_details_check = now
                try:
                    maybe_fetch_details()
                except Exception as e:
                    log.error("vehicle details check failed: %s", e)
                    main_db._conn = None
            if main_db is not None:
                if new_points.is_set() and data_seen_at is None:
                    data_seen_at = now
                due = last_derive is None or now - last_derive >= DERIVE_EVERY
                try:
                    changed = settings_changed_at(main_db)
                except Exception:
                    changed = settings_seen
                    main_db._conn = None
                if changed != settings_seen:
                    settings_seen = changed
                    due = True  # e.g. fuel grade changed in the web UI
                if data_seen_at is not None and now - data_seen_at >= DERIVE_AFTER_DATA:
                    due = True
                if due:
                    new_points.clear()
                    data_seen_at = None
                    last_derive = now
                    try:
                        for trips, fills in rebuild_all(main_db).values():
                            log.info("derived %d trip(s), %d fill-up(s)", trips, fills)
                    except Exception as e:
                        log.error("deriving trips/fill-ups failed: %s", e)
                        main_db._conn = None
            if main_db is not None and now - last_checks >= CHECK_EVERY:
                last_checks = now
                if poller is not None and not tokens.expires_soon(margin=120):
                    try:
                        vins = [r[0] for r in main_db.conn.execute("SELECT vin FROM vehicles")]
                        main_db.conn.commit()
                        poller.run_due(vins)
                    except Exception as e:
                        log.error("CarData REST poll failed: %s", e.__class__.__name__)
                        main_db._conn = None
                try:
                    raised = alerts.run(main_db, notifier)
                    if raised:
                        log.info("raised %d alert(s)", raised)
                except Exception as e:
                    log.error("alert check failed: %s", e)
                    main_db._conn = None
            if now - last_summary >= SUMMARY_EVERY:
                log.info(
                    "last %d min: %d message(s), %d data point(s) stored%s",
                    SUMMARY_EVERY // 60,
                    stats["messages"],
                    stats["points"],
                    f", {stats['db_errors']} database error(s)" if stats["db_errors"] else "",
                )
                stats.update(messages=0, points=0, db_errors=0)
                last_summary = now
            stop.wait(30)
    finally:
        client.disconnect()
        client.loop_stop()
        http.close()
        for d in (ingest_db, main_db):
            if d is not None:
                d.close()
    return exit_code


def _redact_topic(topic: str) -> str:
    """Hide GCID/VIN in logs, which people tend to paste into public issues."""
    parts = topic.split("/")
    return "/".join(p if p == "#" else p[:4] + "…" for p in parts)
