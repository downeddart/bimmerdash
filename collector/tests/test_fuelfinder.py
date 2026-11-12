import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bimmerdash.fuelfinder import (
    FuelFinderClient,
    FuelFinderError,
    forecourt_row,
    price_rows,
    sync_once,
)

# Invented forecourts — no real station data in tests.
STATION = {
    "node_id": "node-1",
    "trading_name": "Test Retail Park",
    "brand_name": "TestFuel",
    "is_supermarket_service_station": True,
    "is_motorway_service_station": False,
    "temporary_closure": False,
    "location": {"latitude": 51.5, "longitude": -0.12, "postcode": "ZZ1 1ZZ"},
    "fuel_types": ["E10", "B7"],
    "fuel_prices": [
        {
            "fuel_type": "E10",
            "price": 134.9,
            "price_change_effective_timestamp": "2026-11-08T07:00:00Z",
        },
        {"fuel_type": "B7", "price": None},
    ],
}


def make_client(handler, **kw):
    return FuelFinderClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        "cid",
        "secret",
        min_interval=0,
        sleep=lambda s: None,
        **kw,
    )


def token_response():
    return httpx.Response(
        200, json={"data": {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}}
    )


def test_pagination_auth_and_end_of_data():
    seen = []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/oauth/generate_access_token"):
            assert json.loads(req.content) == {"client_id": "cid", "client_secret": "secret"}
            return token_response()
        assert req.headers["authorization"] == "Bearer tok"
        batch = int(req.url.params["batch-number"])
        seen.append(batch)
        if batch == 1:
            return httpx.Response(200, json={"data": [STATION] * 500})
        if batch == 2:
            return httpx.Response(200, json={"data": [STATION] * 3})
        pytest.fail("should stop after a short page")

    pages = list(make_client(handler).forecourts())
    assert [len(p) for p in pages] == [500, 3] and seen == [1, 2]


def test_404_past_last_page_ends_iteration():
    def handler(req):
        if "oauth" in req.url.path:
            return token_response()
        if req.url.params["batch-number"] == "1":
            return httpx.Response(200, json=[STATION] * 500)
        return httpx.Response(404, json={"message": "batch not found"})

    assert [len(p) for p in make_client(handler).prices()] == [500]


def test_incremental_since_format():
    def handler(req):
        if "oauth" in req.url.path:
            return token_response()
        assert req.url.params["effective-start-timestamp"] == "2026-11-09 06:00:00"
        return httpx.Response(200, json={"data": []})

    since = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
    assert list(make_client(handler).prices(since)) == []


def test_reauth_on_401_and_retry_on_429():
    calls = {"auth": 0, "get": 0}

    def handler(req):
        if "oauth" in req.url.path:
            calls["auth"] += 1
            return token_response()
        calls["get"] += 1
        if calls["get"] == 1:
            return httpx.Response(401)
        if calls["get"] == 2:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"data": [STATION]})

    assert len(next(make_client(handler).forecourts())) == 1
    assert calls["auth"] == 2


def test_bad_credentials():
    def handler(req):
        return httpx.Response(401)

    with pytest.raises(FuelFinderError, match="rejected"):
        next(make_client(handler).forecourts())


def test_parsing():
    row = forecourt_row(STATION)
    assert row[:6] == ("node-1", "Test Retail Park", "TestFuel", "ZZ1 1ZZ", 51.5, -0.12)
    assert row[6:9] == (False, True, False)
    observed = datetime(2026, 11, 9, tzinfo=UTC)
    assert price_rows(STATION, observed) == [
        ("node-1", "E10", datetime(2026, 11, 8, 7, tzinfo=UTC), 134.9)
    ]


def test_parsing_tolerates_wrong_types():
    # Upstream format drift must not raise: bad fields become None, bad prices are skipped.
    odd = {
        "node_id": "node-2",
        "trading_name": ["not", "text"],
        "location": "not a dict",
        "is_motorway_service_station": "yes",
        "fuel_types": ["E10", 5],
        "fuel_prices": [
            "junk",
            {"fuel_type": "E10", "price": [1], "price_change_effective_timestamp": "x"},
            {"fuel_type": "E5", "price": "139.9", "price_change_effective_timestamp": 1767261660},
        ],
    }
    row = forecourt_row(odd)
    assert row[1:8] == (None, None, None, None, None, None, None)
    assert row[9] == ["E10"]
    observed = datetime(2026, 11, 9, tzinfo=UTC)
    assert price_rows(odd, observed) == [("node-2", "E5", observed, 139.9)]


class FakeDb:
    def __init__(self, state):
        self.state = state
        self.prices = []
        self.forecourts = []

    def fuel_finder_state(self):
        return dict(self.state)

    def set_fuel_finder_state(self, **kw):
        self.state.update(kw)

    def upsert_forecourts(self, page):
        self.forecourts += page
        return len(page)

    def insert_fuel_prices(self, rows):
        self.prices += rows
        return len(rows)


class FakeClient:
    def __init__(self):
        self.since = "unset"

    def forecourts(self):
        yield [STATION]

    def prices(self, since=None):
        self.since = since
        yield [STATION]


def test_sync_first_run_is_full():
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    db = FakeDb({"forecourts_at": None, "full_prices_at": None, "incremental_at": None})
    client = FakeClient()
    assert sync_once(client, db, now) == {"forecourts": 1, "prices": 1}
    assert client.since is None
    assert db.state == {"forecourts_at": now, "full_prices_at": now, "incremental_at": now}


def test_sync_then_incremental_with_overlap():
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    last = now - timedelta(minutes=30)
    db = FakeDb({"forecourts_at": now, "full_prices_at": now, "incremental_at": last})
    client = FakeClient()
    sync_once(client, db, now)
    assert client.since == last - timedelta(hours=2)
    assert db.forecourts == []  # forecourts are fresh
    assert db.state["incremental_at"] == now and db.state["full_prices_at"] == now
