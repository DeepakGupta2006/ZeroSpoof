"""API-key auth, signed one-time client tokens, rate limiting, replay protection, webhook signing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Header, HTTPException, Request

from .config import settings


# ---------------------------------------------------------------- client tokens
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_client_token(session_id: str, key_id: str, nonce: str) -> str:
    payload = _b64(json.dumps({"sid": session_id, "kid": key_id, "exp": int(time.time()) + settings.token_ttl_s, "n": nonce}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(settings.token_secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_client_token(token: str) -> dict[str, Any] | None:
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(settings.token_secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        if data["exp"] < time.time():
            return None
        return data
    except Exception:
        return None


# ---------------------------------------------------------------- webhook signatures
def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def verify_webhook(secret: str, header: str, body: bytes, tolerance_s: int = 300) -> bool:
    """Reference verifier customers can copy (see docs/API.md)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
        if abs(time.time() - t) > tolerance_s:
            return False
        return hmac.compare_digest(parts["v1"], sign_webhook(secret, t, body).split("v1=")[1])
    except Exception:
        return False


# ---------------------------------------------------------------- FastAPI dependencies
class Principal(dict):
    @property
    def key_id(self) -> str:
        return self["id"]

    @property
    def is_admin(self) -> bool:
        return self["role"] == "admin"


async def authenticate(request: Request, x_api_key: str | None = Header(default=None),
                       x_zs_nonce: str | None = Header(default=None),
                       x_zs_timestamp: str | None = Header(default=None)) -> Principal:
    app = request.app
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key header")
    key = await _run(app.state.db.lookup_key, x_api_key)
    if not key:
        raise HTTPException(401, "Invalid API key")

    # Per-key rate limit (requests / minute)
    n = await app.state.store.incr_window(f"key:{key['id']}", 60)
    if n > settings.api_rate_per_min:
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "30"})

    # Replay protection for state-changing calls: unique nonce + fresh timestamp.
    if request.method in ("POST", "DELETE", "PUT", "PATCH"):
        if x_zs_nonce or settings.require_nonce:
            if not x_zs_nonce or not x_zs_timestamp:
                raise HTTPException(400, "X-ZS-Nonce and X-ZS-Timestamp are required")
            try:
                ts = int(x_zs_timestamp)
            except ValueError:
                raise HTTPException(400, "Bad X-ZS-Timestamp")
            if abs(time.time() - ts) > 300:
                raise HTTPException(400, "Timestamp outside the 5 minute window")
            if not await app.state.store.setnx(f"nonce:{key['id']}:{x_zs_nonce}", 600):
                raise HTTPException(409, "Replayed nonce")
    return Principal(key)


async def require_admin(p: Principal) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "Admin key required")
    return p


async def _run(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)
