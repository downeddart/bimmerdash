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
def client():
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
    assert client.get("/api/status").json()["last_message_at"]
    assert client.get("/api/vehicles/not-a-vin/overview").status_code == 404
