"""Command line: bimmerdash {login,stream,keys}."""

from __future__ import annotations

import argparse
import logging
import os
import sys

import httpx

from .auth import AuthError, TokenStore, device_login
from .config import Config, ConfigError
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bimmerdash")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="authorise with BMW (device code flow)")
    sub.add_parser("stream", help="record the CarData MQTT stream")
    keys = sub.add_parser("keys", help="list data keys received so far")
    keys.add_argument("--redact", action="store_true", help="hide values (safe to share)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("BIMMERDASH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = Config.from_env(require_client_id=args.command != "keys")
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if args.command == "login":
        return cmd_login(cfg)
    if args.command == "stream":
        return run_stream(cfg)
    return cmd_keys(cfg, args.redact)


if __name__ == "__main__":
    sys.exit(main())
