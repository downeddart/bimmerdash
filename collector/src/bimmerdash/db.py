"""PostgreSQL/TimescaleDB access: migrations, ingest, vehicles, API-call log."""

from __future__ import annotations

import logging
from datetime import timedelta
from importlib import resources

import psycopg
from psycopg.types.json import Jsonb

from .records import data_points, parse_time, payload_hash, vin_of

log = logging.getLogger(__name__)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations from the package, in filename order. Returns those applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files("bimmerdash.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    applied = []
    for f in files:
        version = f.name.removesuffix(".sql")
        if version in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        log.info("applied migration %s", version)
        applied.append(version)
    return applied


class Database:
    """One connection, reconnecting on demand. Not thread-safe: one per thread."""

    def __init__(self, url: str):
        self.url = url
        self._conn: psycopg.Connection | None = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.url, autocommit=False)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()

    def migrate(self) -> list[str]:
        return migrate(self.conn)

    def ingest(self, record: dict, commit: bool = True) -> int:
        """Store one raw record and its data points. Idempotent. Returns points stored."""
        try:
            n = self._ingest(record)
            if commit:
                self.conn.commit()
            return n
        except psycopg.OperationalError:
            # Connection dropped: reconnect once and retry.
            self._conn = None
            n = self._ingest(record)
            if commit:
                self.conn.commit()
            return n

    def _ingest(self, record: dict) -> int:
        conn = self.conn
        payload = record["payload"]
        received_at = parse_time(record["received_at"])
        vin = vin_of(record)
        cur = conn.execute(
            "INSERT INTO raw_messages (received_at, topic, vin, payload, payload_hash)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (received_at, record["topic"], vin, Jsonb(payload), payload_hash(payload)),
        )
        if cur.rowcount == 0:
            return 0  # already ingested
        points = data_points(record)
        if vin and points:
            conn.execute(
                "INSERT INTO vehicles (vin, first_seen) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (vin, received_at),
            )
        for p in points:
            row = (p.time, p.vin, p.key, Jsonb(p.value), p.value_num, p.unit)
            conn.execute(
                "INSERT INTO telemetry (time, vin, key, value, value_num, unit)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                row,
            )
            conn.execute(
                "INSERT INTO latest_values (time, vin, key, value, value_num, unit)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (vin, key) DO UPDATE SET time = EXCLUDED.time,"
                " value = EXCLUDED.value, value_num = EXCLUDED.value_num, unit = EXCLUDED.unit"
                " WHERE latest_values.time <= EXCLUDED.time",
                row,
            )
        return len(points)

    def commit(self) -> None:
        self.conn.commit()

    # --- vehicles -------------------------------------------------------------

    def vehicles_needing_details(self, max_age: timedelta) -> list[str]:
        rows = self.conn.execute(
            "SELECT vin FROM vehicles WHERE details_fetched_at IS NULL"
            " OR details_fetched_at < now() - %s",
            (max_age,),
        ).fetchall()
        self.conn.commit()
        return [r[0] for r in rows]

    def save_vehicle_details(
        self, vin: str, basic: dict | None, image: bytes | None, image_type: str | None
    ) -> None:
        basic = basic or {}
        self.conn.execute("INSERT INTO vehicles (vin) VALUES (%s) ON CONFLICT DO NOTHING", (vin,))
        self.conn.execute(
            "UPDATE vehicles SET details_fetched_at = now(),"
            " model_name = COALESCE(%s, model_name), series = COALESCE(%s, series),"
            " body_type = COALESCE(%s, body_type), colour = COALESCE(%s, colour),"
            " basic_data = COALESCE(%s, basic_data),"
            " image = COALESCE(%s, image), image_type = COALESCE(%s, image_type)"
            " WHERE vin = %s",
            (
                basic.get("modelName"),
                basic.get("seriesDevt") or basic.get("series"),
                basic.get("bodyType"),
                basic.get("colourDescription"),
                Jsonb(basic) if basic else None,
                image,
                image_type,
                vin,
            ),
        )
        self.conn.commit()

    # --- API quota --------------------------------------------------------------

    def api_calls_last_24h(self) -> int:
        n = self.conn.execute(
            "SELECT count(*) FROM api_calls WHERE time > now() - interval '24 hours'"
        ).fetchone()[0]
        self.conn.commit()
        return n

    def log_api_call(self, endpoint: str, status: int | None) -> None:
        self.conn.execute(
            "INSERT INTO api_calls (endpoint, status) VALUES (%s, %s)", (endpoint, status)
        )
        self.conn.commit()
