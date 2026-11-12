"""UK average weekly pump prices from DESNZ (published on GOV.UK every Monday or Tuesday).

The CSV attachments move to new URLs with each release, so they are found through the
GOV.UK Content API rather than hard-coded.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

CONTENT_API = "https://www.gov.uk/api/content/government/statistics/weekly-road-fuel-prices"
COLUMNS = {"petrol": "ULSP", "diesel": "ULSD"}
MAX_BYTES = 20_000_000  # the CSVs are a few hundred KB


def _gov_uk(url: str) -> bool:
    """Only fetch attachments over HTTPS from GOV.UK, whatever the API response says."""
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "gov.uk" or host.endswith(".gov.uk"))


def _get_text(http: httpx.Client, url: str) -> str:
    with http.stream("GET", url) as r:
        r.raise_for_status()
        body = bytearray()
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError(f"{url} is larger than {MAX_BYTES} bytes")
        return body.decode(r.encoding or "utf-8", errors="replace")


def csv_urls(http: httpx.Client) -> list[str]:
    r = http.get(CONTENT_API)
    r.raise_for_status()
    details = r.json().get("details")
    attachments = details.get("attachments") if isinstance(details, dict) else None
    urls = [
        a["url"]
        for a in attachments or []
        if isinstance(a, dict)
        and isinstance(a.get("url"), str)
        and (a.get("content_type") == "text/csv" or a["url"].endswith(".csv"))
    ]
    skipped = [u for u in urls if not _gov_uk(u)]
    if skipped:
        log.warning("ignoring %d CSV link(s) not on GOV.UK", len(skipped))
    urls = [u for u in urls if _gov_uk(u)]
    if not urls:
        raise ValueError("no CSV attachments found on the weekly road fuel prices page")
    return urls


def parse_csv(text: str) -> list[tuple[date, str, float]]:
    """Rows of (week_start, fuel, pence per litre) from one DESNZ CSV."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header:
        raise ValueError("empty CSV")
    cols = {}
    for fuel, code in COLUMNS.items():
        for i, name in enumerate(header):
            if name.startswith(code) and "pump price" in name.lower():
                cols[fuel] = i
    if "Date" not in header[0] or len(cols) != 2:
        raise ValueError(f"unexpected CSV header: {header[:3]}")
    rows = []
    for line in reader:
        if not line or not line[0].strip():
            continue
        week = datetime.strptime(line[0].strip(), "%d/%m/%Y").date()
        for fuel, i in cols.items():
            value = line[i].strip() if i < len(line) else ""
            if value:
                rows.append((week, fuel, float(value)))
    return rows


def fetch_all(http: httpx.Client) -> list[tuple[date, str, float]]:
    rows = []
    for url in csv_urls(http):
        rows += parse_csv(_get_text(http, url))
    return rows
