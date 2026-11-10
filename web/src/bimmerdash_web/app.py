"""bimmerdash web: JSON API over the collector's database, plus the built frontend."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from . import auth, derived, queries, settings

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

    app = FastAPI(
        title="bimmerdash",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    limiter = auth.RateLimiter()

    def current_user(token: str | None) -> str | None:
        with pool.connection() as conn:
            return auth.session_user(conn, token) if auth.ready(conn) else None

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not auth.enabled() or not path.startswith("/api/") or path in auth.PUBLIC_API:
            return await call_next(request)
        user = await run_in_threadpool(current_user, request.cookies.get(auth.COOKIE))
        if user is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        request.state.user = user
        return await call_next(request)

    def set_cookie(request: Request, response: Response, token: str) -> None:
        response.set_cookie(
            auth.COOKIE,
            token,
            max_age=auth.SESSION_DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )

    @app.get("/api/auth/state")
    def auth_state(request: Request):
        if not auth.enabled():
            return {"enabled": False, "setup_required": False, "user": None}
        with pool.connection() as conn:
            if not auth.ready(conn):
                raise HTTPException(503, "database not ready; is the collector up to date?")
            return {
                "enabled": True,
                "setup_required": not auth.has_users(conn),
                "user": auth.session_user(conn, request.cookies.get(auth.COOKIE)),
            }

    @app.post("/api/auth/setup")
    def auth_setup(body: auth.NewAccount, request: Request, response: Response):
        """Create the first account. Only works while there are no accounts."""
        if not auth.enabled():
            raise HTTPException(404)
        with pool.connection() as conn, conn.transaction():
            conn.execute("LOCK TABLE web_users IN EXCLUSIVE MODE")
            if auth.has_users(conn):
                raise HTTPException(409, "an account already exists")
            auth.create_user(conn, body.username, body.password)
            token = auth.new_session(conn, body.username, request.headers.get("user-agent"))
        set_cookie(request, response, token)
        return {"user": body.username}

    @app.post("/api/auth/login")
    def auth_login(body: auth.Credentials, request: Request, response: Response):
        if not auth.enabled():
            raise HTTPException(404)
        client = request.client.host if request.client else "unknown"
        wait = limiter.blocked(client)
        if wait:
            raise HTTPException(
                429,
                "too many failed attempts; try again later",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        with pool.connection() as conn:
            if not auth.check_login(conn, body.username, body.password):
                limiter.failed(client)
                raise HTTPException(401, "wrong username or password")
            limiter.reset(client)
            token = auth.new_session(conn, body.username, request.headers.get("user-agent"))
        set_cookie(request, response, token)
        return {"user": body.username}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response):
        with pool.connection() as conn:
            auth.end_session(conn, request.cookies.get(auth.COOKIE))
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    def auth_password(body: auth.PasswordChange, request: Request, response: Response):
        user = getattr(request.state, "user", None)
        if not auth.enabled() or user is None:
            raise HTTPException(404)
        with pool.connection() as conn:
            if not auth.check_login(conn, user, body.current):
                raise HTTPException(403, "current password is wrong")
            auth.create_user(conn, user, body.new)  # also signs out other sessions
            token = auth.new_session(conn, user, request.headers.get("user-agent"))
        set_cookie(request, response, token)
        return {"user": user}

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

    @app.get("/api/settings")
    def get_settings():
        with pool.connection() as conn:
            return settings.get_all(conn)

    @app.put("/api/settings")
    def put_settings(body: settings.GlobalSettings):
        with pool.connection() as conn:
            settings.save(conn, "global", body.model_dump())
            return settings.get_all(conn)

    @app.put("/api/vehicles/{vin}/settings")
    def put_vehicle_settings(vin: str, body: settings.VehicleSettings):
        with pool.connection() as conn:
            vin_or_404(vin)
            if not conn.execute("SELECT 1 FROM vehicles WHERE vin = %s", (vin,)).fetchone():
                raise HTTPException(404, "unknown vehicle")
            settings.save(conn, vin, body.model_dump())
            return settings.get_all(conn)

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


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="bimmerdash-web")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the web UI (default)")
    pw = sub.add_parser("set-password", help="create an account or reset its password")
    pw.add_argument("username")
    args = parser.parse_args(argv)
    if args.command == "set-password":
        return set_password(args.username)

    import uvicorn

    uvicorn.run(
        "bimmerdash_web.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("BIMMERDASH_WEB_PORT", 8320)),
        proxy_headers=True,
        log_level=os.environ.get("BIMMERDASH_LOG_LEVEL", "info").lower(),
    )
    return 0


def set_password(username: str) -> int:
    import getpass
    import sys

    import psycopg

    try:
        auth.Credentials(username=username, password="x")
    except ValueError:
        print("Usernames may use letters, digits and . _ @ -", file=sys.stderr)
        return 2
    if sys.stdin.isatty():
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat it: ") != password:
            print("Passwords don't match.", file=sys.stderr)
            return 1
    else:
        password = sys.stdin.readline().rstrip("\n")
    if len(password) < auth.MIN_PASSWORD:
        print(f"Use at least {auth.MIN_PASSWORD} characters.", file=sys.stderr)
        return 1
    with psycopg.connect(os.environ["BIMMERDASH_DATABASE_URL"], autocommit=True) as conn:
        if not auth.ready(conn):
            print(
                "Database not ready: start the collector once so it can migrate.", file=sys.stderr
            )
            return 1
        auth.create_user(conn, username, password)
    print(f"Password set for {username}. Their other sessions were signed out.")
    return 0
