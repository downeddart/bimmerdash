from datetime import date

import httpx
import pytest

from bimmerdash.ukaverages import CONTENT_API, fetch_all, parse_csv

HEADER = (
    "﻿Date,ULSP (Ultra low sulphur unleaded petrol) Pump price in pence/litre,"
    "ULSD (Ultra low sulphur diesel) Pump price in pence/litre,"
    "ULSP (Ultra low sulphur unleaded petrol) Duty rate in pence/litre,"
    "ULSD (Ultra low sulphur diesel) Duty rate in pence/litre,"
    "ULSP (Ultra low sulphur unleaded petrol) VAT percentage rate,"
    "ULSD (Ultra low sulphur diesel) VAT percentage rate\n"
)
CSV = (
    HEADER
    + "26/10/2026,174.07,199.22,52.95,52.95,20,20\n02/11/2026,172.78,198.58,52.95,52.95,20,20\n\n"
)


def test_parse_csv_uses_pump_price_columns():
    rows = parse_csv(CSV)
    assert rows == [
        (date(2026, 10, 26), "petrol", 174.07),
        (date(2026, 10, 26), "diesel", 199.22),
        (date(2026, 11, 2), "petrol", 172.78),
        (date(2026, 11, 2), "diesel", 198.58),
    ]


def test_parse_csv_rejects_unknown_format():
    with pytest.raises(ValueError):
        parse_csv("Week,Price\n01/01/2026,1\n")


def test_parse_csv_rejects_empty_file():
    with pytest.raises(ValueError):
        parse_csv("")


def test_fetch_all_discovers_csvs_via_content_api():
    def handler(req: httpx.Request):
        if str(req.url) == CONTENT_API:
            return httpx.Response(
                200,
                json={
                    "details": {
                        "attachments": [
                            {
                                "url": "https://assets.publishing.service.gov.uk/a.xlsx",
                                "content_type": "application/x",
                            },
                            {
                                "url": "https://assets.publishing.service.gov.uk/new.csv",
                                "content_type": "text/csv",
                            },
                            {
                                "url": "https://assets.publishing.service.gov.uk/old.csv",
                                "content_type": "text/csv",
                            },
                        ]
                    }
                },
            )
        assert req.url.path in ("/new.csv", "/old.csv")
        return httpx.Response(200, text=CSV)

    rows = fetch_all(httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(rows) == 8


def test_fetch_all_only_follows_gov_uk_links_and_caps_size(monkeypatch):
    from bimmerdash import ukaverages

    fetched = []

    def handler(req: httpx.Request):
        if str(req.url) == CONTENT_API:
            return httpx.Response(
                200,
                json={
                    "details": {
                        "attachments": [
                            {"url": "http://169.254.169.254/x.csv", "content_type": "text/csv"},
                            {"url": "https://evil.example/x.csv", "content_type": "text/csv"},
                            {"url": "https://assets.publishing.service.gov.uk/a.csv"},
                        ]
                    }
                },
            )
        fetched.append(str(req.url))
        return httpx.Response(200, text=CSV)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_all(http)
    assert fetched == ["https://assets.publishing.service.gov.uk/a.csv"]
    monkeypatch.setattr(ukaverages, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        fetch_all(http)
