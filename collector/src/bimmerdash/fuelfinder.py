"""UK Fuel Finder API client and price-history sync.

The API serves every UK forecourt in pages of 500, with OAuth client-credentials auth.
Prices carry the time each change took effect, so storing every change gives a price
history that later lets a detected fill-up be costed at the price on that day.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://www.fuel-finder.service.gov.uk/api/v1"
PAGE_SIZE = 500
MAX_PAGES = 1000  # about 500k rows; the whole UK is a few tens of thousands


class FuelFinderError(Exception):
    pass


class FuelFinderClient:
    def __init__(
        self,
        http: httpx.Client,
        client_id: str,
        client_secret: str,
        base_url: str = BASE_URL,
        min_interval: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval  # be gentle: a full sync is ~40 requests
        self.sleep = sleep
        self._token: str | None = None
        self._refresh: str | None = None
        self._expires = 0.0
        self._last_request = 0.0

    # --- auth -----------------------------------------------------------------

    def _store_token(self, body: dict) -> str:
        data = body.get("data", body)
        self._token = data["access_token"]
        self._refresh = data.get("refresh_token", self._refresh)
        self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        if self._refresh:
            r = self.http.post(
                f"{self.base_url}/oauth/regenerate_access_token",
                json={"client_id": self.client_id, "refresh_token": self._refresh},
            )
            if r.status_code == 200:
                return self._store_token(r.json())
            self._refresh = None
        r = self.http.post(
            f"{self.base_url}/oauth/generate_access_token",
            json={"client_id": self.client_id, "client_secret": self.client_secret},
        )
        if r.status_code in (400, 401, 403):
            raise FuelFinderError("Fuel Finder rejected the client ID/secret")
        r.raise_for_status()
        return self._store_token(r.json())

    # --- requests ---------------------------------------------------------------

    def _get(self, path: str, params: dict) -> list[dict]:
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                self.sleep(wait)
            self._last_request = time.monotonic()
            r = self.http.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
            )
            if r.status_code == 401 and attempt == 0:
                self._token = None  # expired early: re-authenticate once
                continue
            if r.status_code == 429:
                self.sleep(min(int(r.headers.get("Retry-After", 30)), 300))
                continue
            if r.status_code == 404 and "batch-number" in params:
                return []  # past the last page
            r.raise_for_status()
            body = r.json()
            data = body.get("data", body) if isinstance(body, dict) else body
            return data if isinstance(data, list) else []
        raise FuelFinderError(f"{path}: gave up after repeated rate limiting")

    def _pages(self, path: str, params: dict | None = None) -> Iterator[list[dict]]:
        batch = 1
        while True:
            page = self._get(path, {**(params or {}), "batch-number": batch})
            if not page:
                return
            yield page
            if len(page) < PAGE_SIZE:
                return
            batch += 1
            if batch > MAX_PAGES:
                # A feed that never ends would page forever; fail this sync instead.
                raise FuelFinderError(f"{path}: more than {MAX_PAGES} pages, giving up")

    def forecourts(self) -> Iterator[list[dict]]:
        return self._pages("/pfs")

    def prices(self, since: datetime | None = None) -> Iterator[list[dict]]:
        params = {}
        if since is not None:
            params["effective-start-timestamp"] = since.astimezone(UTC).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return self._pages("/pfs/fuel-prices", params)


# --- parsing --------------------------------------------------------------------


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def forecourt_row(d: dict) -> tuple:
    loc = d.get("location")
    loc = loc if isinstance(loc, dict) else {}
    fuels = d.get("fuel_types")
    return (
        d["node_id"],
        _text(d.get("trading_name")),
        _text(d.get("brand_name")),
        _text(loc.get("postcode")),
        _float(loc.get("latitude")),
        _float(loc.get("longitude")),
        _flag(d.get("is_motorway_service_station")),
        _flag(d.get("is_supermarket_service_station")),
        bool(d.get("temporary_closure") or d.get("permanent_closure")),
        [f for f in fuels if isinstance(f, str)] if isinstance(fuels, list) else None,
    )


def price_rows(d: dict, observed_at: datetime) -> list[tuple]:
    rows = []
    for fp in d.get("fuel_prices") or []:
        if not isinstance(fp, dict):
            continue
        price = _float(fp.get("price"))
        if price is None or not isinstance(fp.get("fuel_type"), str) or not fp["fuel_type"]:
            continue
        effective = (
            _parse_time(fp.get("price_change_effective_timestamp"))
            or _parse_time(fp.get("price_last_updated"))
            or observed_at
        )
        rows.append((d["node_id"], fp["fuel_type"], effective, price))
    return rows


# --- sync loop ------------------------------------------------------------------

FORECOURTS_EVERY = timedelta(hours=24)
FULL_PRICES_EVERY = timedelta(days=7)  # periodic full re-sync heals any gaps
INCREMENTAL_OVERLAP = timedelta(hours=2)  # covers clock/time-zone ambiguity; rows dedupe


def sync_once(client: FuelFinderClient, db, now: datetime | None = None) -> dict:
    """Bring forecourts and prices up to date. Returns counts for logging."""
    now = now or datetime.now(UTC)
    state = db.fuel_finder_state()
    counts = {"forecourts": 0, "prices": 0}

    if state["forecourts_at"] is None or now - state["forecourts_at"] > FORECOURTS_EVERY:
        for page in client.forecourts():
            counts["forecourts"] += db.upsert_forecourts(page)
        db.set_fuel_finder_state(forecourts_at=now)

    full = state["full_prices_at"] is None or now - state["full_prices_at"] > FULL_PRICES_EVERY
    since = None if full else state["incremental_at"] - INCREMENTAL_OVERLAP
    for page in client.prices(since):
        rows = [r for d in page for r in price_rows(d, now)]
        counts["prices"] += db.insert_fuel_prices(rows)
    if full:
        db.set_fuel_finder_state(full_prices_at=now, incremental_at=now)
    else:
        db.set_fuel_finder_state(incremental_at=now)
    return counts


UK_AVERAGES_EVERY = timedelta(hours=24)


def sync_uk_averages(http: httpx.Client, db, now: datetime | None = None) -> int | None:
    """Refresh DESNZ weekly UK averages once a day. Returns rows stored, or None if not due."""
    from .ukaverages import fetch_all

    now = now or datetime.now(UTC)
    last = db.fuel_finder_state().get("uk_averages_at")
    if last is not None and now - last < UK_AVERAGES_EVERY:
        return None
    n = db.upsert_uk_averages(fetch_all(http))
    db.set_fuel_finder_state(uk_averages_at=now)
    return n


def run_prices(cfg, interval: float = 1800) -> int:
    """Long-running price sync (compose service `prices`).

    UK weekly averages (no credentials needed) daily; Fuel Finder station prices every
    `interval` seconds when credentials are configured.
    """
    import signal

    from .db import Database

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    if not cfg.database_url:
        log.error("BIMMERDASH_DATABASE_URL is not set")
        return 2
    has_ff = bool(cfg.fuel_finder_client_id and cfg.fuel_finder_client_secret)
    if not has_ff:
        log.warning(
            "Fuel Finder not configured (BIMMERDASH_FUEL_FINDER_CLIENT_ID/_SECRET):"
            " syncing UK weekly averages only"
        )

    db = Database(cfg.database_url)
    db.migrate()
    with httpx.Client(timeout=60, headers={"User-Agent": "bimmerdash"}) as http:
        client = (
            FuelFinderClient(
                http, cfg.fuel_finder_client_id, cfg.fuel_finder_client_secret, sleep=stop.wait
            )
            if has_ff
            else None
        )
        while not stop.is_set():
            try:
                n = sync_uk_averages(http, db)
                if n is not None:
                    log.info("UK weekly average prices synced: %d row(s)", n)
            except Exception as e:  # unexpected upstream data must never stop the worker
                log.error("UK average price sync failed: %s: %s", e.__class__.__name__, e)
                db._conn = None
            if client is not None:
                try:
                    counts = sync_once(client, db)
                    log.info(
                        "fuel prices synced: %d forecourt(s) updated, %d new price change(s)",
                        counts["forecourts"],
                        counts["prices"],
                    )
                except FuelFinderError as e:
                    log.error("%s", e)
                except Exception as e:  # unexpected upstream data must never stop the worker
                    log.error("fuel price sync failed: %s: %s", e.__class__.__name__, e)
                    db._conn = None
            stop.wait(interval)
    db.close()
    return 0
