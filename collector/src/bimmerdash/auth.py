"""BMW CarData authentication: OAuth device-code flow with PKCE, plus token refresh.

The user approves access in their own browser; this code never sees their BMW password.
The MQTT stream uses the GCID as username and the id_token as password.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

DEVICE_CODE_URL = "https://customer.bmwgroup.com/gcdm/oauth/device/code"
TOKEN_URL = "https://customer.bmwgroup.com/gcdm/oauth/token"
SCOPES = "authenticate_user openid cardata:streaming:read cardata:api:read"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class AuthError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge)."""
    verifier = _b64url(secrets.token_bytes(32))
    return verifier, pkce_challenge(verifier)


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    id_token: str
    gcid: str
    expires_at: float  # unix time

    def expires_soon(self, margin: float = 300, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - margin

    @classmethod
    def from_response(cls, data: dict, previous: Tokens | None = None) -> Tokens:
        """Build from a token endpoint response, keeping fields a refresh may omit."""
        try:
            return cls(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token") or (previous and previous.refresh_token),
                id_token=data.get("id_token") or (previous and previous.id_token),
                gcid=data.get("gcid") or (previous and previous.gcid),
                expires_at=time.time() + int(data.get("expires_in", 3600)),
            )
        except KeyError as e:
            raise AuthError(f"token response missing {e}") from None


class TokenStore:
    """Tokens on disk as JSON, readable only by the owner."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        return Tokens(**json.loads(self.path.read_text()))

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(tokens), f)
        os.replace(tmp, self.path)


def device_login(
    client_id: str,
    http: httpx.Client,
    prompt: Callable[[str, str, int], None],
    sleep: Callable[[float], None] = time.sleep,
) -> Tokens:
    """Run the device-code flow. `prompt(url, user_code, expires_in)` tells the user what to do."""
    verifier, challenge = make_pkce()
    r = http.post(
        DEVICE_CODE_URL,
        data={
            "client_id": client_id,
            "response_type": "device_code",
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        headers={"Accept": "application/json"},
    )
    if r.status_code != 200:
        raise AuthError(f"device code request failed: HTTP {r.status_code} {r.text[:200]}")
    dc = r.json()
    # BMW dropped verification_uri_complete, so build it ourselves.
    url = f"{dc['verification_uri']}?user_code={dc['user_code']}"
    expires_in = int(dc.get("expires_in", 300))
    interval = int(dc.get("interval", 5))
    prompt(url, dc["user_code"], expires_in)

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        sleep(interval)
        r = http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": dc["device_code"],
                "grant_type": DEVICE_GRANT,
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
        body = r.json() if r.content else {}
        if r.status_code == 200:
            return Tokens.from_response(body)
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise AuthError(
            f"login failed: {error or r.status_code} {body.get('error_description', '')}"
        )
    raise AuthError("login timed out: the code was not approved in time")


def refresh(client_id: str, tokens: Tokens, http: httpx.Client) -> Tokens:
    r = http.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": client_id,
        },
        headers={"Accept": "application/json"},
    )
    if r.status_code != 200:
        raise AuthError(f"token refresh failed: HTTP {r.status_code} {r.text[:200]}")
    return Tokens.from_response(r.json(), previous=tokens)
