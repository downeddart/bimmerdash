"""UK average weekly pump prices from DESNZ (published on GOV.UK every Monday or Tuesday).

The CSV attachments move to new URLs with each release, so they are found through the
GOV.UK Content API rather than hard-coded.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

import httpx

log = logging.getLogger(__name__)

CONTENT_API = "https://www.gov.uk/api/content/government/statistics/weekly-road-fuel-prices"
COLUMNS = {"petrol": "ULSP", "diesel": "ULSD"}


def csv_urls(http: httpx.Client) -> list[str]:
    r = http.get(CONTENT_API)
    r.raise_for_status()
    attachments = r.json().get("details", {}).get("attachments", [])
    urls = [
        a["url"]
        for a in attachments
        if a.get("url") and (a.get("content_type") == "text/csv" or a["url"].endswith(".csv"))
    ]
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
        r = http.get(url)
        r.raise_for_status()
        rows += parse_csv(r.text)
    return rows
