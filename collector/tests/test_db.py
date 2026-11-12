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


def test_recompress_images_converts_once(db):
    db.ingest(rec("2026-11-09T08:00:01+00:00", 1, "2026-11-09T08:00:00Z"))
    db.save_vehicle_details(VIN, None, b"old-png", "image/png")
    assert db.recompress_images(lambda data: (b"webp:" + data, "image/webp")) == 1
    assert q(db, "SELECT image, image_type FROM vehicles") == [(b"webp:old-png", "image/webp")]
    assert db.recompress_images(lambda data: pytest.fail("already WebP")) == 0


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


OTHER_VIN = "WBA0000000TEST001"


def _point(vin, ts, data):
    return {
        "received_at": ts,
        "topic": f"00000000-0000-0000-0000-000000000000/{vin}",
        "payload": {
            "vin": vin,
            "timestamp": ts,
            "data": {k: {"value": v, "timestamp": ts} for k, v in data.items()},
        },
    }


def test_rebuild_skips_placeholders_and_isolates_cars(db, monkeypatch):
    from bimmerdash import derive

    door, odo = derive.DOORS, derive.ODOMETER
    # The car sometimes sends "-" for a number; that point is skipped, not fatal.
    for vin in (VIN, OTHER_VIN):
        for ts, data in [
            ("2026-11-09T07:00:00+00:00", {door: "SECURED", odo: 1000}),
            ("2026-11-09T07:30:00+00:00", {odo: "-"}),
            ("2026-11-09T08:00:00+00:00", {door: "UNLOCKED", odo: 1000}),
            ("2026-11-09T08:30:00+00:00", {door: "LOCKED", odo: 1020}),
        ]:
            db.ingest(_point(vin, ts, data))
    out = derive.rebuild_all(db)
    assert out[VIN][0] == 1 and out[OTHER_VIN][0] == 1

    # And if one car's rebuild fails for any reason, the others still run.
    real = derive.rebuild

    def flaky(d, vin, now=None):
        if vin == VIN:
            raise ValueError("boom")
        return real(d, vin, now)

    monkeypatch.setattr(derive, "rebuild", flaky)
    out = derive.rebuild_all(db)
    assert VIN not in out and out[OTHER_VIN][0] == 1


def test_alerts_one_car_failing_does_not_stop_others(db, monkeypatch):
    from bimmerdash import alerts

    for vin in (VIN, OTHER_VIN):
        db.ingest(_point(vin, "2026-11-10T16:00:00+00:00", {alerts.FUEL_PERCENT: 5}))
    real = alerts.evaluate

    def flaky(conn, vin, *a, **k):
        if vin == VIN:
            raise ValueError("math domain error")
        return real(conn, vin, *a, **k)

    monkeypatch.setattr(alerts, "evaluate", flaky)

    class Quiet:
        configured = False

    now = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    assert alerts.run(db, Quiet(), now) == 1
    assert q(db, "SELECT vin, kind FROM alerts") == [(OTHER_VIN, "low_fuel")]


def test_alerts_ignore_non_finite_position(db):
    from bimmerdash import alerts

    lat = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
    lon = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
    db.ingest(
        _point(VIN, "2026-11-10T16:00:00+00:00", {alerts.FUEL_PERCENT: 5, lat: "inf", lon: -1.0})
    )

    class Quiet:
        configured = False

    assert alerts.run(db, Quiet(), datetime(2026, 11, 10, 17, 0, tzinfo=UTC)) == 1


def test_import_raw_skips_bad_records(db, tmp_path, capsys):
    import json

    from bimmerdash.__main__ import cmd_import_raw
    from bimmerdash.config import Config

    raw = tmp_path / "raw"
    raw.mkdir()
    good = rec("2026-01-01T10:00:00+00:00", 100, "2026-01-01T10:00:00Z")
    bad = rec("2026-01-01T10:01:00+00:00", 101, "2026-01-01T10:01:00Z")
    bad["payload"]["data"]["vehicle.vehicle.travelledDistance"]["timestamp"] = 1767261660
    bad["payload"]["timestamp"] = None
    later = rec("2026-01-02T10:00:00+00:00", 102, "2026-01-02T10:00:00Z")
    (raw / "2026-01-01.jsonl").write_text(
        json.dumps(good)
        + "\n"
        + '{"received_at": "2026-01-01T10:00:30+00:00", "top\n'
        + json.dumps(bad)
        + "\n"
    )
    (raw / "2026-01-02.jsonl").write_text(json.dumps(later) + "\n")
    cfg = Config(client_id="", vin=None, data_dir=tmp_path, database_url=URL)
    assert cmd_import_raw(cfg) == 0
    # The truncated line is skipped; the int timestamp falls back to received_at.
    assert q(db, "SELECT count(*) FROM raw_messages") == [(3,)]
    assert "Skipped 1 unreadable record(s)" in capsys.readouterr().out


def test_poller_backs_off_only_the_failing_car(db, tmp_path):
    import httpx

    from bimmerdash.cardata_api import CarDataApi
    from bimmerdash.telematic import TelematicPoller

    polled = []

    def handler(request):
        if request.method == "POST":
            return httpx.Response(201, json={"containerId": "c-1"})
        vin = request.url.path.split("/")[3]
        polled.append(vin)
        if vin == VIN:
            return httpx.Response(500)
        return httpx.Response(200, json={"telematicData": {}})

    api = CarDataApi(httpx.Client(transport=httpx.MockTransport(handler)), lambda: "t", db)
    poller = TelematicPoller(api, db, None, timedelta(hours=4))
    now = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    poller.run_due([VIN, OTHER_VIN, "not-a-vin"], now)
    assert polled == [VIN, OTHER_VIN]  # the failure didn't stop the next car
    polled.clear()
    poller.run_due([VIN, OTHER_VIN], now + timedelta(minutes=10))
    assert VIN not in polled  # backed off for 30 min


def test_fuel_finder_state_rejects_unknown_fields(db):
    with pytest.raises(ValueError):
        db.set_fuel_finder_state(**{"forecourts_at = now(); --": None})


def test_alert_delivery_retries_and_is_capped(db):
    import httpx

    from bimmerdash import alerts

    sent, fail = [], [True]

    class FlakyNotifier:
        configured = True

        def send(self, alert):
            if fail[0]:
                raise httpx.ConnectError("down")
            sent.append(alert)

    cc = [{"type": f"W{i}", "severity": "HIGH"} for i in range(8)]
    db.ingest(_point(VIN, "2026-11-10T16:00:00+00:00", {alerts.CHECK_CONTROL: cc}))
    alerts._last_try.clear()
    now = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    assert alerts.run(db, FlakyNotifier(), now) == 8
    errors = q(db, "SELECT count(*) FROM alerts WHERE send_error IS NOT NULL")[0][0]
    assert errors == alerts.MAX_PUSHES_PER_CYCLE  # capped, and the failures are recorded
    fail[0] = False
    alerts._last_try.clear()  # as if RETRY_EVERY had passed
    alerts.run(db, FlakyNotifier(), now + timedelta(minutes=10))
    assert len(sent) == alerts.MAX_PUSHES_PER_CYCLE and all(a.priority == 1 for a in sent)
    alerts.run(db, FlakyNotifier(), now + timedelta(minutes=20))
    assert len(sent) == 8
    assert q(db, "SELECT count(*) FROM alerts WHERE sent_at IS NULL")[0][0] == 0


def test_raw_files_are_private(tmp_path):
    import stat

    from bimmerdash.stream import RawWriter

    raw = tmp_path / "raw"
    raw.mkdir(mode=0o755)
    old = raw / "2026-01-01.jsonl"
    old.write_text("{}\n")
    old.chmod(0o644)
    w = RawWriter(raw)
    w.write("t", b"{}", now=datetime(2026, 1, 2, tzinfo=UTC))
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)  # noqa: E731
    assert mode(raw) == 0o700 and mode(old) == 0o600
    assert mode(raw / "2026-01-02.jsonl") == 0o600
