"""Login for the web UI: scrypt password hashes, database-backed sessions, rate limiting.

Set BIMMERDASH_AUTH=off to disable (only when something in front of bimmerdash, such as
an SSO proxy, already requires a login).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

import psycopg
from pydantic import BaseModel, Field

COOKIE = "bimmerdash_session"
SESSION_DAYS = 30
MIN_PASSWORD = 10
# scrypt cost: ~16 MB and ~50 ms per hash. Stored with each hash so it can be raised later.
N, R, P = 2**14, 8, 1

# Endpoints reachable without a session.
PUBLIC_API = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}


def enabled() -> bool:
    return os.environ.get("BIMMERDASH_AUTH", "on").strip().lower() not in ("off", "false", "0")


# --- passwords ---------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


# A fixed hash to check against when the username doesn't exist, so a wrong username
# takes as long as a wrong password.
_DUMMY = hash_password(secrets.token_hex(8))


# --- rate limiting -----------------------------------------------------------------


class RateLimiter:
    """Failed attempts per key (client address, or account): at most `limit` in `window`
    seconds. attempt() checks and records in one step, so parallel requests can't all
    slip past the limit; a success calls reset()."""

    def __init__(self, limit: int = 10, window: float = 900):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque | None:
        q = self._fails.get(key)
        if q is None:
            return None
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            del self._fails[key]  # don't keep an entry per address forever
            return None
        return q

    def attempt(self, key: str) -> float:
        """Reserve an attempt. Returns 0 if allowed (counted as a failure until reset),
        else the seconds until another attempt is allowed."""
        now = time.monotonic()
        with self._lock:
            for k in list(self._fails):  # logins are rare, so a full sweep is cheap
                if k != key:
                    self._trim(k, now)
            q = self._trim(key, now)
            if q is not None and len(q) >= self.limit:
                return max(0.0, self.window - (now - q[0]))
            self._fails.setdefault(key, deque()).append(now)
            return 0.0

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

    def __len__(self) -> int:
        return len(self._fails)


# --- storage -----------------------------------------------------------------------


def ready(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT to_regclass('web_users') IS NOT NULL").fetchone()[0]


def has_users(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM web_users)").fetchone()[0]


def create_user(conn: psycopg.Connection, username: str, password: str) -> None:
    conn.execute(
        "INSERT INTO web_users (username, password_hash) VALUES (%s, %s)"
        " ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
        (username, hash_password(password)),
    )
    # A new password signs out every existing session for that user.
    conn.execute("DELETE FROM web_sessions WHERE username = %s", (username,))


def stored_hash(conn: psycopg.Connection, username: str) -> str | None:
    row = conn.execute(
        "SELECT password_hash FROM web_users WHERE username = %s", (username,)
    ).fetchone()
    return row[0] if row else None


def password_matches(stored: str | None, password: str) -> bool:
    """Checks against a dummy hash for unknown users, so timing doesn't reveal them.
    Call without holding a database connection: scrypt takes a while."""
    ok = verify_password(password, stored or _DUMMY)
    return ok and stored is not None


def check_login(conn: psycopg.Connection, username: str, password: str) -> bool:
    return password_matches(stored_hash(conn, username), password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(conn: psycopg.Connection, username: str, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO web_sessions (token_hash, username, expires_at, user_agent)"
        " VALUES (%s, %s, %s, %s)",
        (
            _token_hash(token),
            username,
            datetime.now(UTC) + timedelta(days=SESSION_DAYS),
            (user_agent or "")[:200],
        ),
    )
    conn.execute("DELETE FROM web_sessions WHERE expires_at < now()")
    return token


def session_user(conn: psycopg.Connection, token: str | None) -> str | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT username FROM web_sessions WHERE token_hash = %s AND expires_at > now()",
        (_token_hash(token),),
    ).fetchone()
    return row[0] if row else None


def end_session(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = %s", (_token_hash(token),))


# --- request bodies ----------------------------------------------------------------


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=1, max_length=256)


class NewAccount(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=MIN_PASSWORD, max_length=256)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=MIN_PASSWORD, max_length=256)
