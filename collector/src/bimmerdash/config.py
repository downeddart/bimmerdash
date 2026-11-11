"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MQTT_HOST = "customer.streaming-cardata.bmwgroup.com"
MQTT_PORT = 9000


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    client_id: str
    vin: str | None
    data_dir: Path
    mqtt_host: str = MQTT_HOST
    mqtt_port: int = MQTT_PORT
    database_url: str | None = None
    api_daily_budget: int = 45  # BMW allows 50 per 24 h; keep headroom for manual use
    rest_poll_hours: float = 4  # CarData REST poll (service, tyres, fuel use); 0 = off
    fuel_finder_client_id: str | None = None
    fuel_finder_client_secret: str | None = None

    @property
    def token_path(self) -> Path:
        return self.data_dir / "tokens.json"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @classmethod
    def from_env(cls, require_client_id: bool = True) -> Config:
        client_id = os.environ.get("BIMMERDASH_CLIENT_ID", "").strip()
        if require_client_id and not client_id:
            raise ConfigError("BIMMERDASH_CLIENT_ID is not set (see .env.example)")
        return cls(
            client_id=client_id,
            vin=os.environ.get("BIMMERDASH_VIN", "").strip() or None,
            data_dir=Path(os.environ.get("BIMMERDASH_DATA_DIR", "./data")),
            mqtt_host=os.environ.get("BIMMERDASH_MQTT_HOST", MQTT_HOST),
            mqtt_port=int(os.environ.get("BIMMERDASH_MQTT_PORT", MQTT_PORT)),
            database_url=os.environ.get("BIMMERDASH_DATABASE_URL") or None,
            api_daily_budget=int(os.environ.get("BIMMERDASH_API_DAILY_BUDGET", 45)),
            rest_poll_hours=float(os.environ.get("BIMMERDASH_REST_POLL_HOURS", 4) or 0),
            fuel_finder_client_id=os.environ.get("BIMMERDASH_FUEL_FINDER_CLIENT_ID") or None,
            fuel_finder_client_secret=os.environ.get("BIMMERDASH_FUEL_FINDER_CLIENT_SECRET")
            or None,
        )
