"""API tests against a real TimescaleDB with the collector's schema.

Needs BIMMERDASH_TEST_DATABASE_URL and the collector package installed (for migrations + ingest).
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from bimmerdash_web.queries import overview

ADMIN_URL = os.environ.get("BIMMERDASH_TEST_DATABASE_URL")
TEST_DB = "bimmerdash_web_test"


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

    fresh_database()
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
    assert s["global"]["distance_unit"] == "mi" and s["global"]["economy_unit"] == "mpg"
    assert s["global"]["alerts"]["low_fuel_percent"] == 20
    assert s["vehicles"][VIN]["fuel_grade"] is None

    s = client.put("/api/settings", json={"distance_unit": "km"}).json()
    assert s["global"]["distance_unit"] == "km" and s["global"]["economy_unit"] == "mpg"
    # Alert settings merge: changing one keeps the others.
    client.put("/api/settings", json={"alerts": {"low_fuel_percent": 15}})
    s = client.put("/api/settings", json={"alerts": {"tyres": False}}).json()
    assert s["global"]["alerts"]["low_fuel_percent"] == 15
    assert s["global"]["alerts"]["tyres"] is False and s["global"]["alerts"]["left_open"] is True
    bad = client.put("/api/settings", json={"alerts": {"low_fuel_percent": 90}})
    assert bad.status_code == 422
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


def test_destinations_group_by_named_place_then_distance():
    from datetime import UTC, datetime

    from bimmerdash_web.usage import destinations, place_at

    t = lambda d: datetime(2026, 11, d, tzinfo=UTC)  # noqa: E731
    home = {"id": 1, "name": "Home", "lat": 51.5, "lon": -1.0, "radius_m": 150}
    ends = [
        {"time": t(1), "lat": 51.5003, "lon": -1.0},  # ~33 m from home
        {"time": t(2), "lat": 51.5, "lon": -1.0005},
        {"time": t(3), "lat": 51.6, "lon": -1.1},  # unnamed, twice ~100 m apart
        {"time": t(4), "lat": 51.6009, "lon": -1.1},
        {"time": t(5), "lat": 52.0, "lon": -1.5},  # far away, once
        {"time": t(6), "lat": None, "lon": None},
    ]
    got = destinations(ends, [home])
    assert [(g["name"], g["visits"]) for g in got] == [("Home", 2), (None, 2), (None, 1)]
    assert got[1]["lat"] == (51.6 + 51.6009) / 2
    assert place_at([home], 51.5003, -1.0)["name"] == "Home"
    assert place_at([home], 51.51, -1.0) is None


def test_usage_and_places(client):
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(
            "UPDATE vehicles SET basic_data = %s",
            (Jsonb({"constructionDate": "2021-03-02T00:00:00.000+0000"}),),
        )
        c.execute(
            "INSERT INTO trips (vin, start_time, end_time, distance_km, end_lat, end_lon)"
            " VALUES (%s, now() - interval '2 h', now() - interval '90 min', 3, 51.5, -1.0),"
            " (%s, now() - interval '1 h', now() - interval '30 min', 12, 51.6, -1.1)",
            (VIN, VIN),
        )
    u = client.get(f"/api/vehicles/{VIN}/usage").json()
    m = u["mileage"]
    # 106,682 km since March 2021: the lifetime rate, as there's under 4 weeks of history.
    assert m["estimate_basis"] == "lifetime" and 17000 < m["estimate_km_per_year"] < 20000
    assert u["stats"]["trips"] == 2 and u["stats"]["short_trip_share"] == 0.5
    assert len(u["trips"]) == 2 and len(u["destinations"]) == 2

    p = client.post("/api/places", json={"name": " Home ", "lat": 51.5, "lon": -1.0}).json()
    assert p["name"] == "Home"
    trips = client.get(f"/api/vehicles/{VIN}/trips").json()["trips"]
    assert [t["end_place"]["name"] if t["end_place"] else None for t in trips] == [None, "Home"]
    dests = client.get(f"/api/vehicles/{VIN}/usage").json()["destinations"]
    assert sorted(d["name"] or "" for d in dests) == ["", "Home"]
    assert client.put(f"/api/places/{p['id']}", json={"name": "House"}).status_code == 200
    assert client.get("/api/places").json()[0]["name"] == "House"
    assert client.post("/api/places", json={"name": "", "lat": 1, "lon": 1}).status_code == 422
    assert client.delete(f"/api/places/{p['id']}").status_code == 200
    assert client.delete(f"/api/places/{p['id']}").status_code == 404


def _latest(c, values: dict, at: str = "now()"):
    for key, value in values.items():
        num = value if isinstance(value, int | float) and not isinstance(value, bool) else None
        c.execute(
            "INSERT INTO latest_values (vin, key, time, value, value_num, unit)"
            f" VALUES (%s, %s, {at}, %s, %s, NULL)"
            " ON CONFLICT (vin, key) DO UPDATE SET value = EXCLUDED.value,"
            " value_num = EXCLUDED.value_num, time = EXCLUDED.time",
            (VIN, key, Jsonb(value), num),
        )


def test_health_endpoint(client):
    tyre = "vehicle.chassis.axle.row{}.wheel.{}.tire.{}"
    with psycopg.connect(URL, autocommit=True) as c:
        _latest(
            c,
            {
                "vehicle.status.conditionBasedServices": [
                    {
                        "type": "OIL",
                        "title": "Engine oil",
                        "status": "OK",
                        "date": "2030-01",
                        "unitOfLengthRemaining": "7560",
                    },
                    {
                        "type": "VEHICLE_TUV",
                        "title": "Statutory vehicle inspection",
                        "status": "OK",
                        "date": "2030-09",
                    },
                ],
                "vehicle.status.checkControlMessages": [{"title": "Washer fluid low"}],
                "vehicle.electronicControlUnit.diagnosticTroubleCodes.raw": "97,3178A;64,8040;",
                tyre.format(1, "left", "pressure"): 207,
                tyre.format(1, "left", "pressureTarget"): 234,
                tyre.format(2, "left", "pressure"): 317,
                tyre.format(2, "left", "pressureTarget"): 317,
            },
        )
        c.execute(
            "INSERT INTO alerts (vin, kind, ref, title, message) VALUES"
            " (%s, 'tyre', 'front left', 'Tyre pressure low', 'x')",
            (VIN,),
        )
    h = client.get(f"/api/vehicles/{VIN}/health").json()
    assert [s["name"] for s in h["services"]] == ["Engine oil", "MOT"]
    assert h["services"][0]["distance_km"] == 7560
    assert h["tyres"]["state"] == "low"
    assert h["tyres"]["wheels"]["front_left"]["below_target_pct"] == 11.5
    assert h["tyres"]["wheels"]["rear_left"]["state"] == "ok"
    assert h["warnings"]["items"][0]["text"] == "Washer fluid low"
    assert h["fault_memory"]["count"] == 2 and h["fault_memory"]["modules"] == 2
    assert h["issues"] == 2 and h["due_soon"] == 0  # tyre + warning
    assert h["alerts"]["open"][0]["ref"] == "front left"


def test_fuel_advisor_counts_the_detour(client):
    with psycopg.connect(URL, autocommit=True) as c:
        _latest(
            c,
            {
                "vehicle.cabin.infotainment.navigation.currentLocation.latitude": 51.5,
                "vehicle.cabin.infotainment.navigation.currentLocation.longitude": -1.0,
                "vehicle.drivetrain.fuelSystem.remainingFuel": 10,
                "vehicle.drivetrain.fuelSystem.level": 20,
                "vehicle.drivetrain.lastRemainingRange": 150,
            },
        )
        c.execute("UPDATE vehicles SET tank_litres = 50")
        # Near: 1 km at 160p. Far: 6 km at 150p. Cheaper, but is it worth the drive?
        c.execute(
            "INSERT INTO forecourts (node_id, trading_name, latitude, longitude) VALUES"
            " ('near', 'NEAR FUEL', 51.509, -1.0), ('far', 'Far Fuel', 51.554, -1.0)"
        )
        c.execute(
            "INSERT INTO fuel_prices (node_id, fuel_type, effective_at, price) VALUES"
            " ('near', 'E10', now() - interval '1 day', 160.0),"
            " ('far', 'E10', now() - interval '1 day', 150.0)"
        )
    a = client.get(f"/api/vehicles/{VIN}/fuel/advisor").json()
    assert a["fill_litres"] == 40 and a["economy"]["source"] == "assumed"
    assert a["nearest"]["name"] == "Near Fuel"
    best = a["best"]
    # 40 L: £64.00 vs £60.00; the 12 km round trip burns ~0.84 L (about £1.26).
    assert best["name"] == "Far Fuel" and 2.5 < best["saving"] < 3.0
    assert a["radius_km"] == 8


def test_fuel_advisor_tolerates_placeholders(client):
    # The car sends "-" for values it doesn't know; that must not break the page.
    with psycopg.connect(URL, autocommit=True) as c:
        _latest(
            c,
            {
                "vehicle.cabin.infotainment.navigation.currentLocation.latitude": "inf",
                "vehicle.cabin.infotainment.navigation.currentLocation.longitude": -1.0,
                "vehicle.drivetrain.fuelSystem.remainingFuel": "-",
                "vehicle.drivetrain.fuelSystem.level": "-",
                "vehicle.drivetrain.lastRemainingRange": "-",
            },
        )
    r = client.get(f"/api/vehicles/{VIN}/fuel/advisor")
    assert r.status_code == 200
    assert r.json()["fuel"]["litres"] is None


def test_car_economy_costs_routes_and_mileage_limit(client):
    fuel_key = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.fuel"
    dist_key = "vehicle.drivetrain.fuelSystem.consumptionOverLifeTime.overall.referenceDistance"
    with psycopg.connect(URL, autocommit=True) as c:
        for days_ago, litres, km in ((40, 7000.0, 100000.0), (1, 7035.0, 100500.0)):
            for key, v in ((fuel_key, litres), (dist_key, km)):
                c.execute(
                    "INSERT INTO telemetry (time, vin, key, value, value_num, unit) VALUES"
                    " (now() - make_interval(days => %s), %s, %s, %s, %s, NULL)",
                    (days_ago, VIN, key, Jsonb(v), v),
                )
        c.execute(
            "INSERT INTO trips (vin, start_time, end_time, distance_km, start_lat, start_lon,"
            " end_lat, end_lon) VALUES"
            " (%s, now() - interval '3 h', now() - interval '150 min', 20, 51.5, -1.0, 51.7, -1.2),"
            " (%s, now() - interval '2 h', now() - interval '90 min', 20, 51.7, -1.2, 51.5, -1.0),"
            " (%s, now() - interval '1 h', now() - interval '50 min', 2, 52.0, -2.0, 52.0, -2.01)",
            (VIN, VIN, VIN),
        )
        c.execute(
            "INSERT INTO uk_average_fuel_prices (week_start, fuel, price)"
            " VALUES (current_date - 3, 'petrol', 150.0)"
        )
        c.execute(
            "UPDATE vehicles SET basic_data = %s",
            (Jsonb({"constructionDate": "2021-03-02T00:00:00.000+0000"}),),
        )
    fuel = client.get(f"/api/vehicles/{VIN}/fuel").json()
    ce = fuel["car_economy"]
    assert ce["recent_l100"] == 7.0 and ce["recent_km"] == 500 and ce["lifetime_l100"] == 7.0

    client.post("/api/places", json={"name": "Home", "lat": 51.5, "lon": -1.0})
    client.post("/api/places", json={"name": "Work", "lat": 51.7, "lon": -1.2})
    u = client.get(f"/api/vehicles/{VIN}/usage").json()
    costs = u["costs"]
    assert costs["economy_source"] == "car_recent" and costs["price_source"] == "uk_average"
    assert costs["pence_per_km"] == 10.5  # 7 L/100 km at 150p
    assert costs["cost_per_year"] == round(u["mileage"]["estimate_km_per_year"] * 0.105)
    routes = {(r["from"], r["to"]): r for r in u["routes"]}
    assert routes[("Home", "Work")]["trips"] == 1 and routes[("Home", "Work")]["cost"] == 2.1
    assert routes[(None, None)]["short"] == 1  # unnamed trips grouped last
    assert u["routes"][-1]["from"] is None

    assert u["mileage"]["limit"] is None
    s = client.put(
        f"/api/vehicles/{VIN}/settings",
        json={"mileage_limit_km": 16093, "mileage_year_start": "2026-03-01"},
    ).json()
    assert s["vehicles"][VIN]["mileage_limit_km"] == 16093
    limit = client.get(f"/api/vehicles/{VIN}/usage").json()["mileage"]["limit"]
    assert limit["limit_km"] == 16093 and limit["year_start"] == "2026-03-01"
    assert limit["used_estimated"] is True  # history starts after the year began
    assert limit["projected_km"] > limit["used_km"]
    s = client.put(f"/api/vehicles/{VIN}/settings", json={"mileage_limit_km": 0}).json()
    assert s["vehicles"][VIN]["mileage_limit_km"] is None
    bad = client.put(f"/api/vehicles/{VIN}/settings", json={"mileage_year_start": "March"})
    assert bad.status_code == 422


def test_built_assets_are_compressed_and_cached(tmp_path, monkeypatch):
    """Hashed build files are cached for good; the page itself is always revalidated."""
    if not URL:
        pytest.skip("BIMMERDASH_TEST_DATABASE_URL not set")
    from bimmerdash_web import app as app_module

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>bimmerdash</title>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('x');\n" * 200)
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path)
    monkeypatch.setenv("BIMMERDASH_AUTH", "off")
    with TestClient(app_module.create_app(URL)) as c:
        js = c.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
        assert js.status_code == 200
        assert js.headers["content-encoding"] == "gzip"
        assert "immutable" in js.headers["cache-control"]
        page = c.get("/fuel", headers={"Accept-Encoding": "gzip"})
        assert page.headers["cache-control"] == "no-cache"


def test_car_image_revalidates_with_etag(client):
    first = client.get(f"/api/vehicles/{VIN}/image")
    assert first.status_code == 200 and first.content == b"img"
    etag = first.headers["etag"]
    again = client.get(f"/api/vehicles/{VIN}/image", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
