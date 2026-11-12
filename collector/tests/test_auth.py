import json
import stat

import httpx
import pytest

from bimmerdash.auth import (
    DEVICE_CODE_URL,
    TOKEN_URL,
    AuthError,
    Tokens,
    TokenStore,
    device_login,
    make_pkce,
    pkce_challenge,
    refresh,
)


def test_pkce_challenge_is_unpadded_base64url_sha256():
    # Cross-check against an independent openssl-style computation (S256, RFC 7636 §4.2).
    import base64
    import hashlib

    verifier = "test-verifier-0123456789abcdefghijklmnopqrstuvwxyz"
    expected = base64.b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    expected = expected.replace("+", "-").replace("/", "_").rstrip("=")
    challenge = pkce_challenge(verifier)
    assert challenge == expected
    assert len(challenge) == 43 and "=" not in challenge


def test_make_pkce_shape():
    verifier, challenge = make_pkce()
    assert len(verifier) == 43 and "=" not in verifier
    assert challenge == pkce_challenge(verifier)


def _tokens(**kw):
    base = dict(access_token="a", refresh_token="r", id_token="i", gcid="g", expires_at=1000.0)
    return Tokens(**(base | kw))


def test_expires_soon():
    t = _tokens(expires_at=1000.0)
    assert not t.expires_soon(margin=300, now=600)
    assert t.expires_soon(margin=300, now=701)


def test_token_store_roundtrip_and_permissions(tmp_path):
    store = TokenStore(tmp_path / "sub" / "tokens.json")
    assert store.load() is None
    store.save(_tokens())
    assert store.load() == _tokens()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_device_login_polls_until_approved():
    calls = {"token": 0}

    def handler(request: httpx.Request):
        form = dict(httpx.QueryParams(request.content.decode()))
        if str(request.url) == DEVICE_CODE_URL:
            assert form["code_challenge_method"] == "S256"
            assert "cardata:streaming:read" in form["scope"]
            return httpx.Response(
                200,
                json={
                    "device_code": "dev",
                    "user_code": "ABCD",
                    "verification_uri": "https://example.test/device",
                    "expires_in": 300,
                    "interval": 1,
                },
            )
        assert str(request.url) == TOKEN_URL
        assert form["device_code"] == "dev" and form["code_verifier"]
        calls["token"] += 1
        if calls["token"] < 3:
            return httpx.Response(400, json={"error": "authorization_pending"})
        return httpx.Response(
            200,
            json={
                "access_token": "acc",
                "refresh_token": "ref",
                "id_token": "idt",
                "gcid": "gc",
                "expires_in": 3600,
            },
        )

    prompts = []
    tokens = device_login(
        "cid", _client(handler), lambda *a: prompts.append(a), sleep=lambda s: None
    )
    assert prompts[0][0] == "https://example.test/device?user_code=ABCD"
    assert (tokens.id_token, tokens.gcid) == ("idt", "gc")
    assert calls["token"] == 3


def test_device_login_denied():
    def handler(request):
        if str(request.url) == DEVICE_CODE_URL:
            return httpx.Response(
                200,
                json={"device_code": "d", "user_code": "u", "verification_uri": "https://x.test"},
            )
        return httpx.Response(400, json={"error": "access_denied"})

    with pytest.raises(AuthError, match="access_denied"):
        device_login("cid", _client(handler), lambda *a: None, sleep=lambda s: None)


def test_refresh_keeps_fields_missing_from_response():
    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "r"
        return httpx.Response(200, json={"access_token": "new", "id_token": "newid"})

    t = refresh("cid", _tokens(), _client(handler))
    assert (t.access_token, t.id_token, t.refresh_token, t.gcid) == ("new", "newid", "r", "g")


def test_refresh_failure():
    with pytest.raises(AuthError):
        refresh("cid", _tokens(), _client(lambda r: httpx.Response(401, text=json.dumps({}))))


def test_token_errors_log_only_oauth_fields():
    from bimmerdash.auth import _error_text

    r = httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "expired", "x": "leak"}
    )
    assert _error_text(r) == "invalid_grant expired"
    assert _error_text(httpx.Response(500, text="<html>secret stack</html>")) == ""


def test_token_store_tightens_an_existing_temp_file(tmp_path):
    import os

    path = tmp_path / "tokens.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("old")
    os.chmod(tmp, 0o644)
    TokenStore(path).save(_tokens())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
