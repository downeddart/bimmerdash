"""BMW CarData REST API (50 requests per 24 h per account), with quota tracking."""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Callable

import httpx
from PIL import Image

from .db import Database

log = logging.getLogger(__name__)

API_BASE = "https://api-cardata.bmwgroup.com"
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class QuotaExceeded(Exception):
    pass


class CarDataApi:
    def __init__(
        self,
        http: httpx.Client,
        access_token: Callable[[], str],
        db: Database,
        daily_budget: int = 45,
    ):
        self.http = http
        self.access_token = access_token
        self.db = db
        self.daily_budget = daily_budget

    def _get(self, path: str, label: str, accept: str) -> httpx.Response:
        used = self.db.api_calls_last_24h()
        if used >= self.daily_budget:
            raise QuotaExceeded(
                f"{used} CarData API calls in the last 24 h (budget {self.daily_budget})"
            )
        status = None
        try:
            r = self.http.get(
                API_BASE + path,
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "x-version": "v1",
                    "Accept": accept,
                },
            )
            status = r.status_code
            return r
        finally:
            self.db.log_api_call(label, status)

    def basic_data(self, vin: str) -> dict:
        _check_vin(vin)
        r = self._get(f"/customers/vehicles/{vin}/basicData", "basicData", "application/json")
        r.raise_for_status()
        return r.json()

    def image(self, vin: str) -> tuple[bytes, str]:
        """The vehicle render, normalised to a cropped PNG."""
        _check_vin(vin)
        r = self._get(f"/customers/vehicles/{vin}/image", "image", "*/*")
        r.raise_for_status()
        return normalise_image(r.content)


def _check_vin(vin: str) -> None:
    if not VIN_RE.match(vin):
        raise ValueError("invalid VIN format")


def normalise_image(data: bytes) -> tuple[bytes, str]:
    """BMW labels renders image/jpeg but sends RGBA PNG: sniff, crop transparent margins."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        img = img.convert("RGBA")
        box = img.getchannel("A").point(lambda v: 255 if v > 4 else 0).getbbox()
        if box:
            img = img.crop(box)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), "image/png"


def fetch_vehicle_details(api: CarDataApi, db: Database, vin: str) -> None:
    """Fetch basic data + image (2 API calls) and store them."""
    basic = image = image_type = None
    try:
        basic = api.basic_data(vin)
    except httpx.HTTPStatusError as e:
        log.warning("basicData failed: HTTP %s", e.response.status_code)
    try:
        image, image_type = api.image(vin)
    except httpx.HTTPStatusError as e:
        log.warning("vehicle image failed: HTTP %s", e.response.status_code)
    except OSError as e:  # PIL could not read it
        log.warning("vehicle image unreadable: %s", e)
    db.save_vehicle_details(vin, basic, image, image_type)
    log.info(
        "vehicle details stored: %s%s",
        basic.get("modelName", "?") if basic else "no basic data",
        ", with image" if image else ", no image",
    )
