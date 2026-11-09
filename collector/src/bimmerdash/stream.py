"""Subscribe to the BMW CarData MQTT stream and append every message, untouched, to JSONL files.

Raw storage comes first so nothing is lost: later processing (trips, fill-ups) can be
re-run over the full history.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

from .auth import AuthError, TokenStore, refresh
from .config import Config

log = logging.getLogger(__name__)


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

    def on_disconnect(_client, _userdata, _flags, reason_code, _props):
        if not stop.is_set():
            log.warning("MQTT disconnected: %s (will reconnect)", reason_code)

    def on_message(_client, _userdata, msg):
        record = writer.write(msg.topic, msg.payload)
        payload = record["payload"]
        n = len(payload.get("data", {})) if isinstance(payload, dict) else 0
        log.info("message: %d data point(s)", n)

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

    client.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
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
            stop.wait(30)
    finally:
        client.disconnect()
        client.loop_stop()
        http.close()
    return exit_code


def _redact_topic(topic: str) -> str:
    """Hide GCID/VIN in logs, which people tend to paste into public issues."""
    parts = topic.split("/")
    return "/".join(p if p == "#" else p[:4] + "…" for p in parts)
