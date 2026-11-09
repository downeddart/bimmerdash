from datetime import UTC, datetime

from bimmerdash.discover import format_table, iter_records, summarise
from bimmerdash.stream import RawWriter, _redact_topic

# Fake identifiers only — never put real VIN/GCID/coordinates in tests.
FAKE_TOPIC = "00000000-0000-0000-0000-000000000000/WBA0000000TEST000"


def _payload(ts, **points):
    return {
        "vin": "WBA0000000TEST000",
        "timestamp": ts,
        "data": {k: {"value": v, "unit": u, "timestamp": ts} for k, (v, u) in points.items()},
    }


def test_writer_and_summary(tmp_path):
    import json

    w = RawWriter(tmp_path)
    t1 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    w.write(
        FAKE_TOPIC,
        json.dumps(
            _payload(
                "2026-11-09T08:00:00Z",
                **{
                    "vehicle.vehicle.travelledDistance": (41000, "km"),
                    "vehicle.drivetrain.fuelSystem.level": (50, "%"),
                },
            )
        ).encode(),
        now=t1,
    )
    w.write(
        FAKE_TOPIC,
        json.dumps(
            _payload("2026-11-09T09:00:00Z", **{"vehicle.vehicle.travelledDistance": (41042, "km")})
        ).encode(),
        now=t2,
    )
    w.write(FAKE_TOPIC, b"not json", now=t2)

    files = list(tmp_path.glob("*.jsonl"))
    assert [f.name for f in files] == ["2026-11-09.jsonl"]

    stats = summarise(iter_records(files))
    odo = stats["vehicle.vehicle.travelledDistance"]
    assert (odo.count, odo.last_value, odo.unit) == (2, 41042, "km")
    assert odo.first_seen == "2026-11-09T08:00:00Z"
    assert stats["vehicle.drivetrain.fuelSystem.level"].count == 1

    table = format_table(stats, redact=True)
    assert "41042" not in table and "<redacted>" in table
    assert "2 distinct keys" in table


def test_redact_topic():
    assert _redact_topic(FAKE_TOPIC) == "0000…/WBA0…"
    assert _redact_topic("abcdefgh/#") == "abcd…/#"
