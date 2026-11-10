"""bimmerdash web: JSON API over the collector's database, plus the built frontend."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool

from . import derived, queries

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
STATIC_DIR = Path(os.environ.get("BIMMERDASH_STATIC_DIR", Path(__file__).parent / "static"))


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or os.environ["BIMMERDASH_DATABASE_URL"]
    pool = ConnectionPool(url, min_size=1, max_size=4, open=False, kwargs={"autocommit": True})

    @asynccontextmanager
    async def lifespan(_app):
        pool.open()
        yield
        pool.close()

    app = FastAPI(title="bimmerdash", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)

    def vin_or_404(vin: str) -> str:
        if not VIN_RE.match(vin):
            raise HTTPException(404, "unknown vehicle")
        return vin

    @app.get("/api/health")
    def health():
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}

    @app.get("/api/status")
    def status():
        with pool.connection() as conn:
            return queries.stream_status(conn)

    @app.get("/api/vehicles")
    def vehicles():
        with pool.connection() as conn:
            return queries.vehicles(conn)

    @app.get("/api/vehicles/{vin}/image")
    def image(vin: str):
        with pool.connection() as conn:
            found = queries.vehicle_image(conn, vin_or_404(vin))
        if found is None:
            raise HTTPException(404, "no image")
        data, ctype = found
        return Response(data, media_type=ctype, headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/vehicles/{vin}/overview")
    def overview(vin: str):
        with pool.connection() as conn:
            return queries.overview(queries.latest(conn, vin_or_404(vin)))

    @app.get("/api/vehicles/{vin}/latest")
    def latest(vin: str):
        with pool.connection() as conn:
            lv = queries.latest(conn, vin_or_404(vin))
        return [{"key": k, **v} for k, v in lv.items()]

    @app.get("/api/vehicles/{vin}/distance/weekly")
    def weekly(vin: str, weeks: int = 8):
        with pool.connection() as conn:
            return queries.weekly_distance(conn, vin_or_404(vin), max(1, min(weeks, 104)))

    @app.get("/api/vehicles/{vin}/trips")
    def trips(vin: str, limit: int = 200):
        with pool.connection() as conn:
            return derived.trips(conn, vin_or_404(vin), max(1, min(limit, 2000)))

    @app.get("/api/vehicles/{vin}/fuel")
    def fuel(vin: str):
        with pool.connection() as conn:
            return derived.fuel(conn, vin_or_404(vin))

    @app.get("/api/vehicles/{vin}/fuel/nearby")
    def nearby(vin: str, radius_km: float = 5):
        with pool.connection() as conn:
            return derived.nearby_prices(conn, vin_or_404(vin), max(0.5, min(radius_km, 30)))

    # Single-page app: real files from the build, everything else falls back to index.html.
    if (STATIC_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (STATIC_DIR / path).resolve()
            if path and f.is_file() and STATIC_DIR.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(STATIC_DIR / "index.html")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "bimmerdash_web.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("BIMMERDASH_WEB_PORT", 8320)),
        proxy_headers=True,
        log_level=os.environ.get("BIMMERDASH_LOG_LEVEL", "info").lower(),
    )
