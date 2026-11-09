"""Command line: bimmerdash {login,stream,keys,migrate,import-raw,fetch-vehicle}."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx

from .auth import AuthError, TokenStore, device_login
from .cardata_api import CarDataApi, QuotaExceeded, fetch_vehicle_details
from .config import Config, ConfigError
from .db import Database
from .discover import format_table, iter_records, summarise
from .stream import run_stream


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


def cmd_import_raw(cfg: Config) -> int:
    """Load the JSONL files into the database. Safe to re-run: duplicates are skipped."""
    db = _require_db(cfg)
    if db is None:
        return 2
    db.migrate()
    files = sorted(cfg.raw_dir.glob("*.jsonl"))
    messages = points = 0
    for i, rec in enumerate(iter_records(files), 1):
        points += db.ingest(rec, commit=False)
        messages += 1
        if i % 1000 == 0:
            db.commit()
    db.commit()
    print(
        f"Read {messages} message(s) from {len(files)} file(s); stored {points} new data point(s)."
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bimmerdash")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="authorise with BMW (device code flow)")
    sub.add_parser("stream", help="record the CarData MQTT stream")
    keys = sub.add_parser("keys", help="list data keys received so far")
    keys.add_argument("--redact", action="store_true", help="hide values (safe to share)")
    sub.add_parser("migrate", help="create/upgrade the database schema")
    sub.add_parser("import-raw", help="load recorded JSONL files into the database")
    sub.add_parser("fetch-vehicle", help="fetch vehicle details and image now (uses API quota)")
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
    if args.command == "fetch-vehicle":
        return cmd_fetch_vehicle(cfg)
    return cmd_keys(cfg, args.redact)


if __name__ == "__main__":
    sys.exit(main())
