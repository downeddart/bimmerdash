import io

import httpx
import pytest
from PIL import Image

from bimmerdash.cardata_api import CarDataApi, QuotaExceeded, normalise_image
from bimmerdash.records import data_points, numeric, payload_hash, vin_of

# Fake identifiers only — never real VIN/GCID/coordinates in tests.
VIN = "WBA0000000TEST000"
TOPIC = f"00000000-0000-0000-0000-000000000000/{VIN}"


def record(data, ts="2026-11-09T20:17:50Z", **payload_extra):
    return {
        "received_at": "2026-11-09T20:17:52.229+00:00",
        "topic": TOPIC,
        "payload": {"vin": VIN, "timestamp": ts, "data": data, **payload_extra},
    }


def test_data_points_types_and_timestamps():
    rec = record(
        {
            "vehicle.vehicle.travelledDistance": {
                "value": 106682,
                "unit": "km",
                "timestamp": "2026-11-09T20:17:49Z",
            },
            "vehicle.body.trunk.isOpen": {"value": False},
            "vehicle.cabin.door.status": {"value": "SECURED"},
        }
    )
    pts = {p.key: p for p in data_points(rec)}
    odo = pts["vehicle.vehicle.travelledDistance"]
    assert (odo.value_num, odo.unit, odo.vin) == (106682.0, "km", VIN)
    assert odo.time.isoformat() == "2026-11-09T20:17:49+00:00"
    # Missing per-point timestamp falls back to the payload timestamp.
    assert pts["vehicle.body.trunk.isOpen"].time.isoformat() == "2026-11-09T20:17:50+00:00"
    assert pts["vehicle.body.trunk.isOpen"].value_num == 0.0
    assert pts["vehicle.cabin.door.status"].value_num is None


def test_data_points_ignores_junk():
    assert data_points({"received_at": "x", "topic": "a/b", "payload": "not json"}) == []
    assert data_points(record("nope")) == []


def test_vin_falls_back_to_topic():
    rec = record({})
    del rec["payload"]["vin"]
    assert vin_of(rec) == VIN


def test_numeric():
    assert numeric(True) == 1.0 and numeric(3) == 3.0 and numeric("3") is None


def test_payload_hash_is_order_independent():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})


def _png(size=(100, 60), box=(20, 10, 70, 50)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste((90, 95, 100, 255), box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_normalise_image_crops_transparent_margin():
    data, ctype = normalise_image(_png())
    assert ctype == "image/webp"
    img = Image.open(io.BytesIO(data))
    assert img.format == "WEBP" and img.size == (50, 40)


def test_normalise_image_keeps_transparency():
    # A render with see-through windows: transparency inside the crop must survive.
    src = Image.open(io.BytesIO(_png()))
    src.paste((0, 0, 0, 0), (30, 20, 40, 30))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    img = Image.open(io.BytesIO(normalise_image(buf.getvalue())[0]))
    assert img.mode == "RGBA" and img.getpixel((15, 15))[3] == 0


class FakeDb:
    def __init__(self, used=0):
        self.used = used
        self.calls = []

    def api_calls_last_24h(self):
        return self.used

    def log_api_call(self, endpoint, status):
        self.calls.append((endpoint, status))


def _api(handler, db):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return CarDataApi(http, lambda: "tok", db, daily_budget=45)


def test_api_sends_headers_and_logs_call():
    def handler(req):
        assert req.headers["x-version"] == "v1"
        assert req.headers["authorization"] == "Bearer tok"
        assert req.url.path == f"/customers/vehicles/{VIN}/basicData"
        return httpx.Response(200, json={"modelName": "320i"})

    db = FakeDb()
    assert _api(handler, db).basic_data(VIN) == {"modelName": "320i"}
    assert db.calls == [("basicData", 200)]


def test_api_image_mislabelled_png():
    db = FakeDb()
    api = _api(
        lambda r: httpx.Response(200, content=_png(), headers={"content-type": "image/jpeg"}), db
    )
    data, ctype = api.image(VIN)
    assert ctype == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def test_api_quota_blocks_without_calling():
    db = FakeDb(used=45)
    api = _api(lambda r: pytest.fail("should not call BMW"), db)
    with pytest.raises(QuotaExceeded):
        api.basic_data(VIN)
    assert db.calls == []


def test_api_rejects_bad_vin():
    with pytest.raises(ValueError):
        _api(lambda r: pytest.fail("no call"), FakeDb()).image("short")
