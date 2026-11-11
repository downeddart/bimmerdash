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

    def _get(
        self, path: str, label: str, accept: str, params: dict | None = None
    ) -> httpx.Response:
        return self._call("GET", path, label, accept, params=params)

    def _call(
        self,
        method: str,
        path: str,
        label: str,
        accept: str = "application/json",
        params: dict | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        used = self.db.api_calls_last_24h()
        if used >= self.daily_budget:
            raise QuotaExceeded(
                f"{used} CarData API calls in the last 24 h (budget {self.daily_budget})"
            )
        status = None
        try:
            r = self.http.request(
                method,
                API_BASE + path,
                params=params,
                json=json,
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

    def create_container(self, name: str, purpose: str, descriptors: list[str]) -> str:
        """A container is BMW's saved list of keys that telematicData returns."""
        r = self._call(
            "POST",
            "/customers/containers",
            "createContainer",
            json={"name": name, "purpose": purpose, "technicalDescriptors": descriptors},
        )
        r.raise_for_status()
        container_id = r.json().get("containerId")
        if not container_id:
            raise ValueError("container created but no containerId returned")
        return container_id

    def telematic_data(self, vin: str, container_id: str) -> dict:
        """{key: {value, unit, timestamp}}, values as BMW's strings."""
        _check_vin(vin)
        r = self._get(
            f"/customers/vehicles/{vin}/telematicData",
            "telematicData",
            "application/json",
            params={"containerId": container_id},
        )
        r.raise_for_status()
        return r.json().get("telematicData") or {}


def _check_vin(vin: str) -> None:
    if not VIN_RE.match(vin):
        raise ValueError("invalid VIN format")


WEBP_QUALITY = 90  # a 1000x600 render: ~40 KB, against ~185 KB as PNG


def normalise_image(data: bytes) -> tuple[bytes, str]:
    """BMW labels renders image/jpeg but sends RGBA PNG: sniff, crop transparent margins,
    store as WebP (with transparency) for a smaller page."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        img = img.convert("RGBA")
        box = img.getchannel("A").point(lambda v: 255 if v > 4 else 0).getbbox()
        if box:
            img = img.crop(box)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue(), "image/webp"


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
