"""Command line: bimmerdash <command>. Run `bimmerdash --help` for the list."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import timedelta

import httpx
import psycopg

from .auth import AuthError, TokenStore, device_login
from .cardata_api import CarDataApi, QuotaExceeded, fetch_vehicle_details
from .config import Config, ConfigError
from .db import Database
from .discover import format_table, iter_lines, iter_records, summarise
from .stream import run_stream

log = logging.getLogger(__name__)


def cmd_login(cfg: Config) -> int:
    def prompt(url: str, code: str, expires_in: int) -> None:
        print(
            f"\nOpen this link, sign in with your BMW ID and approve access:\n\n  {url}\n\n"
            f"Code: {code}  (valid for {expires_in // 60} min)\nWaiting for approval…",
            flush=True,
        )

    with httpx.Client(timeout=15) as http:
        try:
            tokens = device_login(cfg.client_id, http, prompt)
        except AuthError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    TokenStore(cfg.token_path).save(tokens)
    print(f"Logged in. Tokens saved to {cfg.token_path}")
    return 0


def cmd_keys(cfg: Config, redact: bool) -> int:
    files = list(cfg.raw_dir.glob("*.jsonl"))
    if not files:
        print(f"No data yet in {cfg.raw_dir}. Run `bimmerdash stream` and use the car.")
        return 1
    print(format_table(summarise(iter_records(files)), redact=redact))
    return 0


def _require_db(cfg: Config) -> Database | None:
    if not cfg.database_url:
        print("Error: BIMMERDASH_DATABASE_URL is not set", file=sys.stderr)
        return None
    return Database(cfg.database_url)


def cmd_migrate(cfg: Config) -> int:
    db = _require_db(cfg)
    if db is None:
        return 2
    applied = db.migrate()
    print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date.")
    return 0


def cmd_derive(cfg: Config) -> int:
    from .derive import rebuild_all

    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    for i, (trips, fills) in enumerate(rebuild_all(db).values(), 1):
        print(f"Car {i}: {trips} trip(s), {fills} fill-up(s)")
    return 0


def cmd_import_raw(cfg: Config) -> int:
    """Load the JSONL files into the database. Safe to re-run: duplicates are skipped."""
    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    files = sorted(cfg.raw_dir.glob("*.jsonl"))
    messages = points = skipped = 0
    conn = db.conn
    for i, (where, line) in enumerate(iter_lines(files), 1):
        # Each record in its own savepoint: a bad line is skipped, not fatal, so the
        # replay always gets past it. A lost connection still stops the run.
        conn.execute("SAVEPOINT record")
        try:
            points += db._ingest(json.loads(line))
            conn.execute("RELEASE SAVEPOINT record")
            messages += 1
        except psycopg.OperationalError:
            raise
        except Exception as e:
            conn.execute("ROLLBACK TO SAVEPOINT record")
            skipped += 1
            log.warning("skipped %s: %s", where, e.__class__.__name__)
        if i % 1000 == 0:
            db.commit()
    db.commit()
    print(
        f"Read {messages} message(s) from {len(files)} file(s); stored {points} new data point(s)."
        + (f" Skipped {skipped} unreadable record(s); see the log." if skipped else "")
    )
    return 0


def cmd_fetch_vehicle(cfg: Config) -> int:
    """Fetch basic data + image now (2 API calls per car). Uses the collector's current token."""
    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    tokens = TokenStore(cfg.token_path).load()
    if tokens is None:
        print("Error: not logged in; run `bimmerdash login`", file=sys.stderr)
        return 1
    if tokens.expires_soon(margin=120):
        print("Token is being refreshed by the collector; try again in a minute.", file=sys.stderr)
        return 1
    vins = [r[0] for r in db.conn.execute("SELECT vin FROM vehicles ORDER BY first_seen")]
    if not vins:
        print("No vehicles known yet: wait for the stream to deliver a message.")
        return 1
    with httpx.Client(timeout=30) as http:
        api = CarDataApi(http, lambda: tokens.access_token, db, cfg.api_daily_budget)
        try:
            for vin in vins:
                fetch_vehicle_details(api, db, vin)
        except QuotaExceeded as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    print(f"Updated {len(vins)} vehicle(s). API calls in last 24 h: {db.api_calls_last_24h()}")
    return 0


def cmd_poll(cfg: Config) -> int:
    """Fetch service, tyre, warning and fuel-use values now (1 API call per car)."""
    from .stream import RawWriter
    from .telematic import TelematicPoller

    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    tokens = TokenStore(cfg.token_path).load()
    if tokens is None:
        print("Error: not logged in; run `bimmerdash login`", file=sys.stderr)
        return 1
    if tokens.expires_soon(margin=120):
        print("Token is being refreshed by the collector; try again in a minute.", file=sys.stderr)
        return 1
    vins = [r[0] for r in db.conn.execute("SELECT vin FROM vehicles ORDER BY first_seen")]
    db.conn.commit()
    with httpx.Client(timeout=30) as http:
        api = CarDataApi(http, lambda: tokens.access_token, db, cfg.api_daily_budget)
        poller = TelematicPoller(api, db, RawWriter(cfg.raw_dir), timedelta(0))
        for i, vin in enumerate(vins, 1):
            try:
                n = poller.poll(vin)
            except (QuotaExceeded, httpx.HTTPError, ValueError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                print(f"Error: {f'HTTP {status}' if status else e}", file=sys.stderr)
                return 1
            print(f"Car {i}: {n} value(s) received")
    print(f"API calls in last 24 h: {db.api_calls_last_24h()}")
    return 0


def cmd_prices(cfg: Config, once: bool) -> int:
    from .fuelfinder import FuelFinderClient, FuelFinderError, run_prices, sync_once
    from .ukaverages import fetch_all

    if not once:
        return run_prices(cfg)
    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    with httpx.Client(timeout=60, headers={"User-Agent": "bimmerdash"}) as http:
        n = db.upsert_uk_averages(fetch_all(http))
        print(f"UK weekly averages: {n} row(s).")
        if not (cfg.fuel_finder_client_id and cfg.fuel_finder_client_secret):
            print("Fuel Finder not configured: skipping station prices.")
            return 0
        client = FuelFinderClient(http, cfg.fuel_finder_client_id, cfg.fuel_finder_client_secret)
        try:
            counts = sync_once(client, db)
        except FuelFinderError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    print(f"{counts['forecourts']} forecourt(s) updated, {counts['prices']} new price change(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bimmerdash")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="authorise with BMW (device code flow)")
    sub.add_parser("stream", help="record the CarData MQTT stream")
    keys = sub.add_parser("keys", help="list data keys received so far")
    keys.add_argument("--redact", action="store_true", help="hide values (safe to share)")
    sub.add_parser("migrate", help="create/upgrade the database schema")
    sub.add_parser("import-raw", help="load recorded JSONL files into the database")
    sub.add_parser("derive", help="rebuild trips and fill-ups from stored data")
    sub.add_parser("fetch-vehicle", help="fetch vehicle details and image now (uses API quota)")
    sub.add_parser("poll", help="fetch service, tyres, warnings and fuel use now (uses API quota)")
    prices = sub.add_parser("prices", help="keep UK fuel price history in sync (Fuel Finder)")
    prices.add_argument("--once", action="store_true", help="sync once and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("BIMMERDASH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs, which contain the VIN. Keep them out of shareable logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cfg = Config.from_env(require_client_id=args.command in ("login", "stream"))
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if args.command == "login":
        return cmd_login(cfg)
    if args.command == "stream":
        return run_stream(cfg)
    if args.command == "migrate":
        return cmd_migrate(cfg)
    if args.command == "import-raw":
        return cmd_import_raw(cfg)
    if args.command == "derive":
        return cmd_derive(cfg)
    if args.command == "fetch-vehicle":
        return cmd_fetch_vehicle(cfg)
    if args.command == "poll":
        return cmd_poll(cfg)
    if args.command == "prices":
        return cmd_prices(cfg, args.once)
    return cmd_keys(cfg, args.redact)


if __name__ == "__main__":
    sys.exit(main())
