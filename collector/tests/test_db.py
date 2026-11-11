"""Database tests against a real TimescaleDB. Set BIMMERDASH_TEST_DATABASE_URL to run them."""

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from bimmerdash.db import Database

ADMIN_URL = os.environ.get("BIMMERDASH_TEST_DATABASE_URL")
TEST_DB = "bimmerdash_test"
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BIMMERDASH_TEST_DATABASE_URL not set")


def _test_url(admin_url: str) -> str:
    """Tests get their own database, recreated for each test (see fresh_database)."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(admin_url)
    params["dbname"] = TEST_DB
    return make_conninfo(**params)


def fresh_database() -> None:
    """Drop and recreate the test database.

    Not DROP SCHEMA: that drops TimescaleDB while its background worker may be using the
    catalog, which deadlocks now and then. WITH (FORCE) disconnects the worker first.
    """
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")


URL = _test_url(ADMIN_URL) if ADMIN_URL else None

VIN = "WBA0000000TEST000"
TOPIC = f"00000000-0000-0000-0000-000000000000/{VIN}"


@pytest.fixture
def db():
    fresh_database()
    d = Database(URL)
    assert d.migrate() == [
        "001_init",
        "002_fuel_prices",
        "003_uk_average_prices",
        "004_trips_fillups",
        "005_settings",
        "006_web_auth",
        "007_places",
        "008_health_alerts",
    ]
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


def test_concurrent_migrations_wait_for_each_other():
    """Collector and prices start together; only one may apply the migrations."""
    from concurrent.futures import ThreadPoolExecutor

    from bimmerdash.db import MIGRATION_LOCK

    fresh_database()
    holder = psycopg.connect(URL, autocommit=True)
    holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
    dbs = [Database(URL) for _ in range(3)]
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(d.migrate) for d in dbs]
        holder.execute("SELECT pg_sleep(0.5)")
        assert not any(f.done() for f in futures)  # all waiting on the lock
        holder.close()  # releases it
        results = [f.result(timeout=60) for f in futures]
    for d in dbs:
        d.close()
    assert sorted(len(r) for r in results) == [0, 0, 8]


def test_migrations_are_committed(db):
    db.close()  # a migrate-only run (the `migrate` command) must leave the schema behind
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT to_regclass('trips') IS NOT NULL").fetchone()[0]


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


def test_uk_average_at(db):
    from datetime import date

    rows = [(date(2026, 10, 26), "petrol", 174.07), (date(2026, 11, 2), "petrol", 172.78)]
    assert db.upsert_uk_averages(rows) == 2
    db.upsert_uk_averages([(date(2026, 11, 2), "petrol", 172.5)])  # revision overwrites
    assert db.uk_average_at("petrol", date(2026, 10, 30)) == (174.07, date(2026, 10, 26))
    assert db.uk_average_at("petrol", date(2026, 11, 9))[0] == 172.5
    assert db.uk_average_at("petrol", date(2020, 1, 1)) is None
    assert db.fuel_finder_state()["uk_averages_at"] is None


def test_rebuild_derives_trips_and_fillups(db):
    from bimmerdash.derive import rebuild

    def msg(ts, data):
        return {
            "received_at": ts.replace("Z", "+00:00"),
            "topic": TOPIC,
            "payload": {
                "vin": VIN,
                "timestamp": ts,
                "data": {k: {"value": v, "timestamp": ts} for k, v in data.items()},
            },
        }

    door = "vehicle.cabin.door.status"
    odo = "vehicle.vehicle.travelledDistance"
    lat = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
    lon = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
    litres = "vehicle.drivetrain.fuelSystem.remainingFuel"
    pct = "vehicle.drivetrain.fuelSystem.level"
    for ts, data in [
        (
            "2026-11-09T07:00:00Z",
            {door: "SECURED", odo: 1000, litres: 10, pct: 20, lat: 51.5, lon: -1.0},
        ),
        ("2026-11-09T08:00:00Z", {door: "UNLOCKED", odo: 1000}),
        ("2026-11-09T08:05:00Z", {door: "LOCKED", odo: 1003}),
        # stopped at a forecourt to fill up
        ("2026-11-09T08:15:00Z", {door: "UNLOCKED", odo: 1006, lat: 51.52, lon: -0.97}),
        ("2026-11-09T08:25:00Z", {door: "LOCKED", odo: 1006, litres: 50, pct: 95}),
        ("2026-11-09T08:40:00Z", {door: "SECURED", odo: 1015, lat: 51.5, lon: -1.0}),
    ]:
        db.ingest(msg(ts, data))
    db.upsert_forecourts(
        [
            {
                "node_id": "n1",
                "trading_name": "Fake Fuel",
                "location": {"latitude": 51.5201, "longitude": -0.97},
            }
        ]
    )
    db.insert_fuel_prices([("n1", "E10", "2026-11-01T00:00:00+00:00", 150.0)])

    assert rebuild(db, VIN) == (1, 1)
    trip = q(db, "SELECT distance_km, fuel_start_l, fuel_end_l FROM trips")
    assert trip == [(15.0, 10.0, 50.0)]
    fill = q(db, "SELECT litres_added, node_id, price_source, cost_gbp FROM fillups")
    assert fill == [(40.0, "n1", "station", 60.0)]
    assert q(db, "SELECT fuel_grade, fuel_kind FROM vehicles") == [("E10", "petrol")]
    assert rebuild(db, VIN) == (1, 1)  # rebuilding replaces, never duplicates
    assert q(db, "SELECT count(*) FROM fillups")[0][0] == 1

    # A grade chosen in the web UI wins; with no E5 price there, it falls back to... nothing
    # (no UK averages loaded in this test), so the cost is unknown rather than wrong.
    db.conn.execute(
        "INSERT INTO settings (scope, key, value) VALUES (%s, 'fuel_grade', '\"E5\"')", (VIN,)
    )
    db.conn.commit()
    rebuild(db, VIN)
    assert q(db, "SELECT fuel_grade, price_source, cost_gbp FROM fillups") == [("E5", None, None)]


def test_telematic_poller_creates_container_once_and_stores_values(db, tmp_path):
    import httpx

    from bimmerdash.cardata_api import CarDataApi
    from bimmerdash.stream import RawWriter
    from bimmerdash.telematic import CBS, TelematicPoller

    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(201, json={"containerId": "c-1"})
        assert request.url.params["containerId"] == "c-1"
        return httpx.Response(
            200,
            json={
                "telematicData": {
                    CBS: {
                        "value": '[{"type": "OIL", "title": "Engine oil", "date": "2027-01"}]',
                        "unit": None,
                        "timestamp": "2026-11-10T16:49:00.000Z",
                    },
                    "vehicle.chassis.axle.row1.wheel.left.tire.pressure": {
                        "value": "207",
                        "unit": "kPa",
                        "timestamp": "2026-11-10T16:49:00.000Z",
                    },
                }
            },
        )

    api = CarDataApi(httpx.Client(transport=httpx.MockTransport(handler)), lambda: "t", db)
    db.ingest(rec("2026-11-10T16:00:00+00:00", 106000, "2026-11-10T16:00:00Z"))
    poller = TelematicPoller(api, db, RawWriter(tmp_path), timedelta(hours=4))
    now = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    assert poller.due(VIN, now)
    assert poller.poll(VIN, now) == 2
    assert poller.poll(VIN, now + timedelta(hours=5)) == 2
    assert [c[0] for c in calls] == ["POST", "GET", "GET"]  # container made once
    assert not poller.due(VIN, now + timedelta(hours=6))  # last poll was an hour before
    assert poller.due(VIN, now + timedelta(hours=10))
    [(value,)] = q(db, "SELECT value FROM latest_values WHERE key = %s", CBS)
    assert value[0]["title"] == "Engine oil"
    [(kpa,)] = q(db, "SELECT value_num FROM latest_values WHERE key LIKE '%%tire.pressure'")
    assert kpa == 207
    assert list(tmp_path.glob("*.jsonl"))  # raw copy kept, like stream messages
    assert q(db, "SELECT count(*) FROM api_calls")[0][0] == 3


def test_alerts_raise_once_then_clear(db):
    from bimmerdash import alerts

    sent = []

    class FakeNotifier:
        configured = True

        def send(self, alert):
            sent.append(alert)

    t = "2026-11-10T16:00:00Z"
    tyre = "vehicle.chassis.axle.row1.wheel.left.tire"

    def push(kpa, at):
        db.ingest(
            {
                "received_at": at,
                "topic": TOPIC,
                "payload": {
                    "vin": VIN,
                    "timestamp": t,
                    "data": {
                        tyre + ".pressure": {"value": kpa, "timestamp": at},
                        tyre + ".pressureTarget": {"value": 234, "timestamp": at},
                        "vehicle.cabin.door.status": {"value": "SECURED", "timestamp": at},
                    },
                },
            }
        )

    push(207, "2026-11-10T16:00:00+00:00")
    now = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    assert alerts.run(db, FakeNotifier(), now) == 1
    assert alerts.run(db, FakeNotifier(), now + timedelta(minutes=1)) == 0  # not again
    assert len(sent) == 1 and sent[0].ref == "front left"
    push(232, "2026-11-10T18:00:00+00:00")
    assert alerts.run(db, FakeNotifier(), now + timedelta(hours=2)) == 0
    rows = q(db, "SELECT kind, sent_at IS NOT NULL, cleared_at IS NOT NULL FROM alerts")
    assert rows == [("tyre", True, True)]
    # Turned off in settings: nothing is evaluated.
    db.conn.execute(
        "INSERT INTO settings (scope, key, value)"
        " VALUES ('global', 'alerts', '{\"enabled\": false}')"
    )
    db.conn.commit()
    push(150, "2026-11-10T19:00:00+00:00")
    assert alerts.run(db, FakeNotifier(), now + timedelta(hours=3)) == 0
