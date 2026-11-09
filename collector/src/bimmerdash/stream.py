"""Subscribe to the BMW CarData MQTT stream and store every message.

Each message is appended untouched to a daily JSONL file first (the safety net), then written
to the database. If the database is unavailable nothing is lost: `bimmerdash import-raw`
replays the JSONL files.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

from .auth import AuthError, TokenStore, refresh
from .cardata_api import CarDataApi, QuotaExceeded, fetch_vehicle_details
from .config import Config
from .db import Database

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 30
SUMMARY_EVERY = 600  # seconds between "received N messages" log lines
DETAILS_MAX_AGE = timedelta(days=30)  # refresh vehicle basic data + image monthly
DETAILS_RETRY = 6 * 3600  # after a failed fetch, wait before trying that VIN again


class RawWriter:
    """Append one JSON line per message to <dir>/YYYY-MM-DD.jsonl (UTC)."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, topic: str, payload: bytes, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            body = payload.decode("utf-8", errors="replace")
        record = {"received_at": now.isoformat(), "topic": topic, "payload": body}
        path = self.directory / f"{now:%Y-%m-%d}.jsonl"
        with self._lock, path.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        return record


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
    ingest_db = Database(cfg.database_url) if cfg.database_url else None

    def on_message(_client, _userdata, msg):
        record = writer.write(msg.topic, msg.payload)
        stats["messages"] += 1
        if ingest_db is None:
            return
        try:
            stats["points"] += ingest_db.ingest(record)
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
    api = (
        CarDataApi(http, lambda: tokens.access_token, main_db, cfg.api_daily_budget)
        if main_db is not None
        else None
    )
    failed_at: dict[str, float] = {}
    last_summary = time.monotonic()
    last_details_check = 0.0  # check soon after start

    def maybe_fetch_details() -> None:
        """Basic data + image for new VINs, refreshed monthly. Runs on this (main) thread."""
        for vin in main_db.vehicles_needing_details(DETAILS_MAX_AGE):
            if time.monotonic() - failed_at.get(vin, -DETAILS_RETRY) < DETAILS_RETRY:
                continue
            if tokens.expires_soon(margin=120):
                return  # wait for the refresh below
            try:
                fetch_vehicle_details(api, main_db, vin)
            except (QuotaExceeded, httpx.HTTPError, ValueError) as e:
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
