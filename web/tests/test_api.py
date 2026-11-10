"""API tests against a real TimescaleDB with the collector's schema.

Needs BIMMERDASH_TEST_DATABASE_URL and the collector package installed (for migrations + ingest).
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from bimmerdash_web.queries import overview

URL = os.environ.get("BIMMERDASH_TEST_DATABASE_URL")
VIN = "WBA0000000TEST000"  # fake
TOPIC = f"00000000-0000-0000-0000-000000000000/{VIN}"


def test_overview_tolerates_missing_keys():
    o = overview({})
    assert o["odometer"] is None and o["location"] is None and o["tyres"] is None


def test_overview_interprets_values():
    t = "2026-11-09T09:00:00+00:00"
    lv = {
        "vehicle.vehicle.travelledDistance": {"value": 106682, "unit": "km", "time": t},
        "vehicle.drivetrain.fuelSystem.level": {"value": 62, "unit": "%", "time": t},
        "vehicle.cabin.window.row1.driver.status": {"value": "CLOSED", "unit": None, "time": t},
        "vehicle.cabin.window.row1.passenger.status": {"value": "OPEN", "unit": None, "time": t},
        "vehicle.status.checkControlMessages": {"value": [], "unit": None, "time": t},
    }
    o = overview(lv)
    assert o["odometer"] == {"value": 106682, "unit": "km"}
    assert o["fuel_percent"] == 62
    assert o["windows_closed"] is False
    assert o["check_control_count"] == 0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BIMMERDASH_AUTH", "off")  # login has its own tests below
    if not URL:
        pytest.skip("BIMMERDASH_TEST_DATABASE_URL not set")
    from bimmerdash.db import Database

    from bimmerdash_web.app import create_app

    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db = Database(URL)
    db.migrate()
    for i, (odo, ts) in enumerate(
        [(106000, "2026-11-02T08:00:00Z"), (106682, "2026-11-09T09:00:00Z")]
    ):
        db.ingest(
            {
                "received_at": ts.replace("Z", "+00:00"),
                "topic": TOPIC,
                "payload": {
                    "vin": VIN,
                    "timestamp": ts,
                    "data": {
                        "vehicle.vehicle.travelledDistance": {"value": odo, "unit": "km"},
                        "vehicle.cabin.door.status": {"value": "SECURED"},
                        "n": {"value": i},
                    },
                },
            }
        )
    db.save_vehicle_details(
        VIN, {"modelName": "320i", "colourDescription": "MINERALGRAU METALLIC"}, b"img", "image/png"
    )
    db.close()
    with TestClient(create_app(URL)) as c:
        yield c


def test_api_endpoints(client):
    assert client.get("/api/health").json() == {"ok": True}
    v = client.get("/api/vehicles").json()
    assert v[0]["vin"] == VIN and v[0]["colour"] == "Mineral Grey" and v[0]["has_image"]
    assert client.get(f"/api/vehicles/{VIN}/image").content == b"img"
    o = client.get(f"/api/vehicles/{VIN}/overview").json()
    assert o["odometer"]["value"] == 106682 and o["doors"] == "SECURED"
    keys = {r["key"] for r in client.get(f"/api/vehicles/{VIN}/latest").json()}
    assert "vehicle.vehicle.travelledDistance" in keys
    status = client.get("/api/status").json()
    assert status["last_message_at"]
    assert status["prices"]["uk_averages"]["status"] == "missing"
    assert status["prices"]["station_prices"]["status"] == "off"
    assert client.get("/api/vehicles/not-a-vin/overview").status_code == 404


def test_classify_freshness():
    from datetime import UTC, date, datetime, timedelta

    from bimmerdash_web.queries import classify_freshness

    now = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)
    f = classify_freshness(
        date(2026, 11, 2), now - timedelta(hours=3), now - timedelta(minutes=20), now
    )
    assert f["uk_averages"]["status"] == "ok" and f["station_prices"]["status"] == "ok"

    # Newest week too old (publication stopped), or checks failing for days.
    assert classify_freshness(date(2026, 10, 26), now, now, now)["uk_averages"]["status"] == "stale"
    stale_check = classify_freshness(date(2026, 11, 2), now - timedelta(days=3), now, now)
    assert stale_check["uk_averages"]["status"] == "stale"
    assert classify_freshness(None, None, None, now)["uk_averages"]["status"] == "missing"

    assert classify_freshness(None, None, None, now)["station_prices"]["status"] == "off"
    old_sync = classify_freshness(None, None, now - timedelta(hours=3), now)
    assert old_sync["station_prices"]["status"] == "stale"


def test_with_intervals_handles_partial_fills():
    from datetime import UTC, datetime

    from bimmerdash_web.derived import monthly, with_intervals

    def f(day, odo, before, after, cost=None, source=None):
        return {
            "time": datetime(2026, 10, day, tzinfo=UTC),
            "odometer": odo,
            "litres_before": before,
            "litres_after": after,
            "cost_gbp": cost,
            "price_source": source,
        }

    fills = with_intervals(
        [f(1, 1000, 10, 50, 60.0, "station"), f(8, 1600, 14, 40, 40.0, "uk_average")]
    )
    assert fills[0]["economy_l100"] is None
    # 50 L in the tank after the first fill, 14 L left before the second: 36 L over 600 km.
    assert (fills[1]["km_since_last"], fills[1]["used_since_last_l"]) == (600, 36)
    assert fills[1]["economy_l100"] == 6.0
    assert monthly(fills) == [{"month": "2026-10", "cost": 100.0, "estimated": 40.0, "fillups": 2}]


def test_trips_and_fuel_endpoints(client):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(
            "INSERT INTO trips (vin, start_time, end_time, distance_km, start_lat, start_lon,"
            " end_lat, end_lon, fuel_used_l, route) VALUES"
            " (%s, now() - interval '1 hour', now() - interval '40 minutes', 12, 51.5, -1.0,"
            "  51.6, -1.1, 1, '[]')",
            (VIN,),
        )
        c.execute(
            "INSERT INTO forecourts (node_id, trading_name, latitude, longitude)"
            " VALUES ('n1', 'Fake Fuel', 51.601, -1.1), ('n2', 'Far Fuel', 52.5, -1.1)"
        )
        c.execute(
            "INSERT INTO fuel_prices (node_id, fuel_type, effective_at, price) VALUES"
            " ('n1', 'E10', now() - interval '1 day', 151.9),"
            " ('n2', 'E10', now() - interval '1 day', 140.9)"
        )
        c.execute(
            "INSERT INTO fillups (vin, time, odometer, litres_before, litres_after,"
            " litres_added, node_id, station, price_ppl, price_source, cost_gbp)"
            " VALUES (%s, now() - interval '2 hours', 106000, 10, 50, 40, 'n1',"
            "  '{\"trading_name\": \"Fake Fuel\"}', 151.9, 'station', 60.76)",
            (VIN,),
        )
    t = client.get(f"/api/vehicles/{VIN}/trips").json()
    assert t["trips"][0]["distance_km"] == 12 and t["trips"][0]["minutes"] == 20
    assert t["trips"][0]["end"] == {"lat": 51.6, "lon": -1.1}
    assert t["summary"]["month_trips"] >= 1

    fuel = client.get(f"/api/vehicles/{VIN}/fuel").json()
    assert fuel["fillups"][0]["station"]["trading_name"] == "Fake Fuel"
    assert fuel["summary"]["total_cost"] == 60.76 and fuel["summary"]["total_estimated"] == 0

    # Only one trip ends there, so "home" falls back to... no live position: nothing nearby.
    near = client.get(f"/api/vehicles/{VIN}/fuel/nearby").json()
    assert near["centre"] is None
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(
            "INSERT INTO latest_values (vin, key, time, value, value_num) VALUES"
            " (%s, 'vehicle.cabin.infotainment.navigation.currentLocation.latitude', now(),"
            "  '51.6', 51.6),"
            " (%s, 'vehicle.cabin.infotainment.navigation.currentLocation.longitude', now(),"
            "  '-1.1', -1.1)",
            (VIN, VIN),
        )
    near = client.get(f"/api/vehicles/{VIN}/fuel/nearby").json()
    assert near["centre"]["source"] == "car"
    assert [s["name"] for s in near["stations"]] == ["Fake Fuel"]  # the other is 100 km away


def test_tidy_name():
    from bimmerdash_web.derived import tidy_name

    assert tidy_name("DURHAM ROAD FILLING STATION,") == "Durham Road Filling Station"
    assert tidy_name("SAINSBURYS TEAM VALLEY") == "Sainsburys Team Valley"
    assert tidy_name("Falcon Service Station") == "Falcon Service Station"
    assert tidy_name("ESSO OF THE NORTH") == "Esso of the North"


def test_settings_round_trip(client):
    s = client.get("/api/settings").json()
    assert s["global"] == {"distance_unit": "mi", "economy_unit": "mpg"}
    assert s["vehicles"][VIN]["fuel_grade"] is None

    s = client.put("/api/settings", json={"distance_unit": "km"}).json()
    assert s["global"] == {"distance_unit": "km", "economy_unit": "mpg"}
    assert client.put("/api/settings", json={"distance_unit": "furlongs"}).status_code == 422

    s = client.put(
        f"/api/vehicles/{VIN}/settings", json={"fuel_grade": "E5", "tank_litres": 59}
    ).json()
    assert s["vehicles"][VIN]["fuel_grade"] == "E5" and s["vehicles"][VIN]["tank_litres"] == 59
    # The nearby-prices grade follows the setting straight away.
    assert client.get(f"/api/vehicles/{VIN}/fuel/nearby").json()["grade"] == "E5"
    s = client.put(f"/api/vehicles/{VIN}/settings", json={"tank_litres": 0}).json()
    assert s["vehicles"][VIN]["tank_litres"] is None
    bad = client.put(f"/api/vehicles/{VIN}/settings", json={"fuel_grade": "LPG"})
    assert bad.status_code == 422
    other = client.put("/api/vehicles/WBA0000000OTHER00/settings", json={"fuel_grade": "E5"})
    assert other.status_code == 404


def test_password_hashing():
    from bimmerdash_web.auth import hash_password, verify_password

    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_rate_limiter():
    from bimmerdash_web.auth import RateLimiter

    rl = RateLimiter(limit=3, window=60)
    for _ in range(3):
        assert rl.blocked("1.2.3.4") == 0
        rl.failed("1.2.3.4")
    assert rl.blocked("1.2.3.4") > 0
    assert rl.blocked("5.6.7.8") == 0  # other clients unaffected
    rl.reset("1.2.3.4")
    assert rl.blocked("1.2.3.4") == 0


def test_login_flow(client, monkeypatch):
    monkeypatch.setenv("BIMMERDASH_AUTH", "on")
    client.cookies.clear()
    # Everything except health and the login endpoints needs a session.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/vehicles").status_code == 401
    assert client.get(f"/api/vehicles/{VIN}/image").status_code == 401
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/auth/state").json() == {
        "enabled": True,
        "setup_required": True,
        "user": None,
    }
    # First account: password rules, then success signs you in.
    assert (
        client.post("/api/auth/setup", json={"username": "me", "password": "short"}).status_code
        == 422
    )
    r = client.post("/api/auth/setup", json={"username": "me", "password": "long enough pw"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/vehicles").status_code == 200
    assert client.get("/api/auth/state").json()["user"] == "me"
    # Setup can't be used again to take over.
    again = client.post("/api/auth/setup", json={"username": "evil", "password": "long enough pw"})
    assert again.status_code == 409

    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/vehicles").status_code == 401
    bad = client.post("/api/auth/login", json={"username": "me", "password": "nope"})
    assert bad.status_code == 401
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
    assert unknown.status_code == 401
    ok = client.post("/api/auth/login", json={"username": "me", "password": "long enough pw"})
    assert ok.status_code == 200 and client.get("/api/vehicles").status_code == 200

    # Changing the password needs the current one, and ends other sessions.
    other = TestClient(client.app)
    other.post("/api/auth/login", json={"username": "me", "password": "long enough pw"})
    wrong = client.post("/api/auth/password", json={"current": "x", "new": "another long pw"})
    assert wrong.status_code == 403
    r = client.post(
        "/api/auth/password", json={"current": "long enough pw", "new": "another long pw"}
    )
    assert r.status_code == 200
    assert client.get("/api/vehicles").status_code == 200
    assert other.get("/api/vehicles").status_code == 401


def test_login_rate_limited(client, monkeypatch):
    monkeypatch.setenv("BIMMERDASH_AUTH", "on")
    client.cookies.clear()
    client.post("/api/auth/setup", json={"username": "me", "password": "long enough pw"})
    client.cookies.clear()
    codes = [
        client.post("/api/auth/login", json={"username": "me", "password": f"guess{i}"}).status_code
        for i in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # Even the right password is refused while blocked.
    right = client.post("/api/auth/login", json={"username": "me", "password": "long enough pw"})
    assert right.status_code == 429


def test_set_password_command(client, monkeypatch, capsys):
    import io

    from bimmerdash_web.app import main

    monkeypatch.setenv("BIMMERDASH_DATABASE_URL", URL)
    monkeypatch.setattr("sys.stdin", io.StringIO("a brand new password\n"))
    assert main(["set-password", "admin"]) == 0
    assert "Password set for admin" in capsys.readouterr().out
    monkeypatch.setenv("BIMMERDASH_AUTH", "on")
    ok = client.post(
        "/api/auth/login", json={"username": "admin", "password": "a brand new password"}
    )
    assert ok.status_code == 200
