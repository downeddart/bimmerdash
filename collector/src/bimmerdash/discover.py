"""Summarise which CarData keys a car actually sends, from the raw JSONL files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class KeyStats:
    key: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    last_value: object = None
    unit: str | None = None


def iter_records(paths: Iterable[Path]):
    for path in sorted(paths):
        with path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def summarise(records: Iterable[dict]) -> dict[str, KeyStats]:
    stats: dict[str, KeyStats] = {}
    for rec in records:
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        for key, point in data.items():
            s = stats.setdefault(key, KeyStats(key))
            ts = (point.get("timestamp") if isinstance(point, dict) else None) or rec["received_at"]
            s.count += 1
            s.first_seen = min(s.first_seen, ts) if s.first_seen else ts
            if ts >= s.last_seen:
                s.last_seen = ts
                if isinstance(point, dict):
                    s.last_value = point.get("value")
                    s.unit = point.get("unit")
                else:
                    s.last_value = point
    return stats


def format_table(stats: dict[str, KeyStats], redact: bool = False) -> str:
    rows = sorted(stats.values(), key=lambda s: s.key)
    lines = [f"{'KEY':<70} {'COUNT':>6}  {'LAST SEEN':<25} VALUE"]
    for s in rows:
        value = "<redacted>" if redact else _short(s.last_value)
        unit = f" {s.unit}" if s.unit and not redact else ""
        lines.append(f"{s.key:<70} {s.count:>6}  {s.last_seen[:25]:<25} {value}{unit}")
    lines.append(f"\n{len(rows)} distinct keys")
    return "\n".join(lines)


def _short(value: object, width: int = 40) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= width else text[: width - 1] + "…"
