"""PostgreSQL/TimescaleDB access: migrations, ingest, vehicles, API-call log."""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from importlib import resources

import psycopg
from psycopg.types.json import Jsonb

from .records import data_points, parse_time, payload_hash, vin_of

log = logging.getLogger(__name__)


# Any fixed number, the same in every process that migrates this database.
MIGRATION_LOCK = 0x626D7764  # "bmwd"


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations from the package, in filename order. Returns those applied.

    Several services start from the same image at once (collector, prices), so migrations
    run under an advisory lock: the first to get it applies them, the rest wait, then find
    nothing left to do.
    """
    conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
    conn.commit()
    try:
        return _migrate(conn)
    finally:
        conn.rollback()  # a failed migration leaves the transaction aborted
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
        conn.commit()


def _migrate(conn: psycopg.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    conn.commit()  # else each migration below is only a savepoint in this open transaction
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

    def recompress_images(self, normalise) -> int:
        """Re-encode stored car images not yet in the current format (one-off after an
        upgrade; BMW's render is only re-fetched monthly). Returns how many changed."""
        rows = self.conn.execute(
            "SELECT vin, image FROM vehicles"
            " WHERE image IS NOT NULL AND image_type IS DISTINCT FROM 'image/webp'"
        ).fetchall()
        done = 0
        for vin, data in rows:
            try:
                image, ctype = normalise(bytes(data))
            except (OSError, ValueError) as e:  # ImageError is a ValueError
                log.warning("stored image for %s… unreadable: %s", vin[:5], e)
                continue
            self.conn.execute(
                "UPDATE vehicles SET image = %s, image_type = %s WHERE vin = %s",
                (image, ctype, vin),
            )
            done += 1
        self.conn.commit()
        return done

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

    # --- fuel prices (UK Fuel Finder) -------------------------------------------

    def fuel_finder_state(self) -> dict:
        row = self.conn.execute(
            "SELECT forecourts_at, full_prices_at, incremental_at, uk_averages_at"
            " FROM fuel_finder_state"
        ).fetchone()
        self.conn.commit()
        return {
            "forecourts_at": row[0],
            "full_prices_at": row[1],
            "incremental_at": row[2],
            "uk_averages_at": row[3],
        }

    def set_fuel_finder_state(self, **fields) -> None:
        allowed = {"forecourts_at", "full_prices_at", "incremental_at", "uk_averages_at"}
        if not set(fields) <= allowed:  # names become SQL identifiers below
            raise ValueError(f"unknown fuel_finder_state fields: {sorted(set(fields) - allowed)}")
        sets = ", ".join(f"{k} = %s" for k in fields)
        self.conn.execute(f"UPDATE fuel_finder_state SET {sets}", tuple(fields.values()))
        self.conn.commit()

    def upsert_forecourts(self, page: list[dict]) -> int:
        from .fuelfinder import forecourt_row

        rows = [(*forecourt_row(d), Jsonb(d)) for d in page if d.get("node_id")]
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO forecourts (node_id, trading_name, brand_name, postcode, latitude,"
                " longitude, is_motorway, is_supermarket, closed, fuel_types, raw)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (node_id) DO UPDATE SET trading_name = EXCLUDED.trading_name,"
                " brand_name = EXCLUDED.brand_name, postcode = EXCLUDED.postcode,"
                " latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,"
                " is_motorway = EXCLUDED.is_motorway, is_supermarket = EXCLUDED.is_supermarket,"
                " closed = EXCLUDED.closed, fuel_types = EXCLUDED.fuel_types,"
                " raw = EXCLUDED.raw, updated_at = now()",
                rows,
            )
        self.conn.commit()
        return len(rows)

    def insert_fuel_prices(self, rows: list[tuple]) -> int:
        """(node_id, fuel_type, effective_at, price) rows; returns how many were new."""
        if not rows:
            return 0
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fuel_prices (node_id, fuel_type, effective_at, price)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )
            new = cur.rowcount
        self.conn.commit()
        return max(new, 0)

    def nearest_forecourts(
        self, lat: float, lon: float, radius_m: float = 150, limit: int = 3
    ) -> list[dict]:
        """Open forecourts within radius_m, closest first (equirectangular distance)."""
        dlat = radius_m / 111_320
        dlon = dlat / max(0.2, abs(math.cos(math.radians(lat))))
        rows = self.conn.execute(
            """
            SELECT node_id, trading_name, brand_name, postcode, latitude, longitude,
                   6371000 * sqrt(power(radians(latitude - %(lat)s), 2)
                     + power(radians(longitude - %(lon)s) * cos(radians(%(lat)s)), 2)) AS dist
            FROM forecourts
            WHERE NOT closed
              AND latitude BETWEEN %(lat)s - %(dlat)s AND %(lat)s + %(dlat)s
              AND longitude BETWEEN %(lon)s - %(dlon)s AND %(lon)s + %(dlon)s
            ORDER BY dist LIMIT %(limit)s
            """,
            {"lat": lat, "lon": lon, "dlat": dlat, "dlon": dlon, "limit": limit},
        ).fetchall()
        self.conn.commit()
        cols = ["node_id", "trading_name", "brand_name", "postcode", "lat", "lon", "distance_m"]
        return [dict(zip(cols, r, strict=True)) for r in rows if r[6] <= radius_m]

    def price_at(self, node_id: str, fuel_type: str, at) -> tuple[float, object] | None:
        """The price in force at `at`: (pence per litre, when it took effect)."""
        row = self.conn.execute(
            "SELECT price, effective_at FROM fuel_prices"
            " WHERE node_id = %s AND fuel_type = %s AND effective_at <= %s"
            " ORDER BY effective_at DESC LIMIT 1",
            (node_id, fuel_type, at),
        ).fetchone()
        self.conn.commit()
        return (row[0], row[1]) if row else None

    def upsert_uk_averages(self, rows: list[tuple]) -> int:
        """(week_start, fuel, price) rows. Revised figures overwrite earlier ones."""
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO uk_average_fuel_prices (week_start, fuel, price) VALUES (%s, %s, %s)"
                " ON CONFLICT (fuel, week_start) DO UPDATE SET price = EXCLUDED.price",
                rows,
            )
        self.conn.commit()
        return len(rows)

    def uk_average_at(self, fuel: str, on) -> tuple[float, object] | None:
        """UK average price (pence/L) for the week containing `on`: (price, week_start)."""
        row = self.conn.execute(
            "SELECT price, week_start FROM uk_average_fuel_prices"
            " WHERE fuel = %s AND week_start <= %s::date ORDER BY week_start DESC LIMIT 1",
            (fuel, on),
        ).fetchone()
        self.conn.commit()
        return (row[0], row[1]) if row else None
