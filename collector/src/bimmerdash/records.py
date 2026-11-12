"""Turn raw CarData MQTT messages into rows. Pure functions, no I/O."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DataPoint:
    time: datetime
    vin: str
    key: str
    value: object
    value_num: float | None
    unit: str | None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _time_or_none(value: object) -> datetime | None:
    """Parse an upstream timestamp; anything that isn't a readable ISO string is None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_time(value)
    except ValueError:
        return None


def payload_hash(payload: object) -> bytes:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode()).digest()


def vin_of(record: dict) -> str | None:
    payload = record.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("vin"), str):
        return payload["vin"]
    last = record.get("topic", "").rsplit("/", 1)[-1]
    return last if len(last) == 17 else None


def numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return None


def data_points(record: dict) -> list[DataPoint]:
    """Data points in a stored record ({received_at, topic, payload})."""
    payload = record.get("payload")
    vin = vin_of(record)
    if not isinstance(payload, dict) or not vin:
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    fallback = _time_or_none(payload.get("timestamp")) or parse_time(record["received_at"])
    points = []
    for key, point in data.items():
        if isinstance(point, dict):
            value, unit, ts = point.get("value"), point.get("unit"), point.get("timestamp")
        else:
            value, unit, ts = point, None, None
        points.append(
            DataPoint(
                time=_time_or_none(ts) or fallback,
                vin=vin,
                key=key,
                value=value,
                value_num=numeric(value),
                unit=unit,
            )
        )
    return points
