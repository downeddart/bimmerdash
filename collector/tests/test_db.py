"""Database tests against a real TimescaleDB. Set BIMMERDASH_TEST_DATABASE_URL to run them."""

import os
from datetime import timedelta

import psycopg
import pytest

from bimmerdash.db import Database

URL = os.environ.get("BIMMERDASH_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BIMMERDASH_TEST_DATABASE_URL not set")

VIN = "WBA0000000TEST000"
TOPIC = f"00000000-0000-0000-0000-000000000000/{VIN}"


@pytest.fixture
def db():
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    d = Database(URL)
    assert d.migrate() == ["001_init", "002_fuel_prices"]
    yield d
    d.close()


def rec(received, odo, ts):
    return {
        "received_at": received,
        "topic": TOPIC,
        "payload": {
            "vin": VIN,
            "timestamp": ts,
            "data": {
                "vehicle.vehicle.travelledDistance": {"value": odo, "unit": "km", "timestamp": ts}
            },
        },
    }


def q(db, sql, *args):
    rows = db.conn.execute(sql, args).fetchall()
    db.conn.commit()
    return rows


def test_migrate_is_idempotent(db):
    assert db.migrate() == []


def test_ingest_dedupes_and_tracks_latest(db):
    r1 = rec("2026-11-09T08:00:01+00:00", 106600, "2026-11-09T08:00:00Z")
    r2 = rec("2026-11-09T09:00:01+00:00", 106682, "2026-11-09T09:00:00Z")
    assert db.ingest(r1) == 1
    assert db.ingest(r1) == 0  # replayed message
    assert db.ingest(r2) == 1
    # An older message arriving late must not overwrite the latest value.
    late = rec("2026-11-09T09:30:00+00:00", 106500, "2026-11-09T07:00:00Z")
    assert db.ingest(late) == 1

    assert q(db, "SELECT count(*) FROM raw_messages")[0][0] == 3
    assert q(db, "SELECT count(*) FROM telemetry")[0][0] == 3
    assert q(db, "SELECT value_num, unit FROM latest_values WHERE vin = %s", VIN) == [
        (106682.0, "km")
    ]
    assert q(db, "SELECT vin FROM vehicles") == [(VIN,)]


def test_vehicle_details_and_api_log(db):
    db.ingest(rec("2026-11-09T08:00:01+00:00", 1, "2026-11-09T08:00:00Z"))
    assert db.vehicles_needing_details(timedelta(days=30)) == [VIN]
    db.save_vehicle_details(
        VIN, {"modelName": "320i", "seriesDevt": "G21", "bodyType": "Touring"}, b"png", "image/png"
    )
    assert db.vehicles_needing_details(timedelta(days=30)) == []
    assert q(db, "SELECT model_name, series, body_type, image_type FROM vehicles") == [
        ("320i", "G21", "Touring", "image/png")
    ]
    db.log_api_call("image", 200)
    assert db.api_calls_last_24h() == 1


def test_fuel_prices_nearest_and_price_at(db):
    from datetime import UTC, datetime

    near = {
        "node_id": "near",
        "trading_name": "Near",
        "location": {"latitude": 51.5000, "longitude": -0.1200, "postcode": "ZZ1 1ZZ"},
    }
    far = {**near, "node_id": "far", "location": {"latitude": 51.51, "longitude": -0.12}}
    closed = {**near, "node_id": "closed", "permanent_closure": True}
    assert db.upsert_forecourts([near, far, closed]) == 3
    assert db.upsert_forecourts([near]) == 1  # update, no duplicate

    hits = db.nearest_forecourts(51.5003, -0.1200, radius_m=150)
    assert [h["node_id"] for h in hits] == ["near"]
    assert 30 < hits[0]["distance_m"] < 40

    t = lambda d, h: datetime(2026, 11, d, h, tzinfo=UTC)  # noqa: E731
    rows = [("near", "E10", t(1, 7), 136.9), ("near", "E10", t(5, 7), 134.9)]
    assert db.insert_fuel_prices(rows) == 2
    assert db.insert_fuel_prices(rows) == 0  # replay is a no-op
    assert db.price_at("near", "E10", t(4, 12)) == (136.9, t(1, 7))
    assert db.price_at("near", "E10", t(9, 12))[0] == 134.9
    assert db.price_at("near", "E10", t(1, 6)) is None
