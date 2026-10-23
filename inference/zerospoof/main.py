"""ZeroSpoof inference API (FastAPI).

REST (customer backend, authenticated with X-API-Key):
    POST   /v1/sessions               create a liveness session -> one-time client token for the browser SDK
    GET    /v1/sessions               list recent sessions
    GET    /v1/sessions/{id}          verdict, per-module scores, flags, score timeline
    GET    /v1/sessions/{id}/audit    tamper-evident audit trail for the session
    POST   /v1/webhooks               register a webhook (session.passed | session.failed | session.review)
    GET    /v1/webhooks               list webhooks
    DELETE /v1/webhooks/{id}          remove a webhook
    GET    /v1/usage                  checks this month + illustrative bill
    POST   /v1/keys                   (admin) create a customer API key
    GET    /v1/audit/verify           (admin) verify the whole hash chain
WebSocket (browser SDK, authenticated with the one-time client token):
    /ws?token=...
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__, billing
from .config import settings
from .db import Database
from .detectors import challenge as challenge_mod
from .detectors import fingerprint as fp_mod
from .engine import LiveSession, ProtocolError
from .security import Principal, authenticate, make_client_token, verify_client_token
from .store import make_store
from .webhooks import deliver_event

log = logging.getLogger("zerospoof")
logging.basicConfig(level=os.getenv("ZS_LOG_LEVEL", "INFO"))

EXPOSE_SCORES = os.getenv("ZS_EXPOSE_SCORES_TO_CLIENT", "1" if settings.env != "prod" else "0") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.env == "prod" and (settings.token_secret.startswith("dev-") or settings.admin_key == "zs_admin_dev"):
        raise RuntimeError("Refusing to start in prod with default dev secrets. Set ZS_TOKEN_SECRET and ZS_ADMIN_KEY.")
    if settings.env == "prod" and settings.demo_relax:
        raise RuntimeError("ZS_DEMO_RELAX must not be enabled in prod.")
    db = Database(settings.database_url)
    await asyncio.to_thread(db.init)
    await asyncio.to_thread(db.seed_dev_keys)
    app.state.db = db
    app.state.store = await make_store(settings.redis_url)
    app.state.live = {}  # sid -> LiveSession
    if settings.env != "prod":
        log.warning("DEV MODE: dev API key '%s' and admin key '%s' are active. Do not expose this to the internet.",
                    settings.dev_api_key, settings.admin_key)
    log.info("ZeroSpoof %s started (store=%s, db=%s)", __version__, app.state.store.kind, db.dialect)
    yield
    await app.state.store.close()


app = FastAPI(title="ZeroSpoof", version=__version__, lifespan=lifespan,
              description="Real-time deepfake & liveness firewall")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


def _db_call(fn, *a, **kw):
    return asyncio.to_thread(fn, *a, **kw)


# ---------------------------------------------------------------- models
class CreateSession(BaseModel):
    external_id: str | None = Field(default=None, max_length=200, description="Your own reference (user id, application id...)")
    challenge: bool = True


class CreateWebhook(BaseModel):
    url: str = Field(pattern=r"^https?://")
    events: list[str] = Field(default_factory=lambda: ["session.failed", "session.review"])


class CreateKey(BaseModel):
    name: str = Field(min_length=1, max_length=120)


# ---------------------------------------------------------------- helpers
async def _present(row: dict[str, Any], timeline: bool = False) -> dict[str, Any]:
    store = app.state.store
    out = {
        "id": row["id"],
        "external_id": row.get("external_id"),
        "status": row["status"],
        "verdict": row["verdict"],
        "probability": row.get("probability"),
        "created_at": row["created_at"],
        "finished_at": row.get("finished_at"),
        **(row.get("summary") or {}),
    }
    if row["status"] == "active":
        live = await store.get_json(f"live:{row['id']}")
        if live:
            out.update({k: live[k] for k in ("verdict", "probability", "modules", "flags", "reasons", "elapsed_s", "latency_ms") if k in live})
            out["fingerprint"] = live.get("fingerprint", out.get("fingerprint"))
        if time.time() - row["created_at"] > settings.token_ttl_s + 900:
            out["status"] = "expired"
    if timeline:
        out["timeline"] = await store.get_scores(row["id"])
    return out


async def _owned_session(sid: str, p: Principal) -> dict[str, Any]:
    row = await _db_call(app.state.db.get_session, sid)
    if not row or (not p.is_admin and row["key_id"] != p.key_id):
        raise HTTPException(404, "Session not found")
    return row


# ---------------------------------------------------------------- REST
@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/docs")


@app.get("/health")
async def health():
    return {"ok": True, "version": __version__, "store": app.state.store.kind, "db": app.state.db.dialect}


@app.post("/v1/sessions", status_code=201)
async def create_session(body: CreateSession, p: Principal = Depends(authenticate)):
    sid = "ses_" + secrets.token_hex(10)
    chall = challenge_mod.new_challenge() if body.challenge else None
    now = time.time()
    await app.state.store.set_json(f"sess:{sid}", {"id": sid, "key_id": p.key_id, "challenge": chall, "created_at": now})
    await _db_call(app.state.db.upsert_session, sid, key_id=p.key_id, external_id=body.external_id, status="active",
                   verdict="pending", created_at=now, summary={})
    await _db_call(app.state.db.audit, "session.created", sid, p.key_id, {"external_id": body.external_id, "challenge": chall["type"] if chall else None})
    token = make_client_token(sid, p.key_id, secrets.token_hex(8))
    return {"session_id": sid, "client_token": token, "ws_url": settings.public_ws_url, "challenge": chall,
            "min_seconds": settings.min_seconds, "expires_in": settings.token_ttl_s}


@app.get("/v1/sessions")
async def list_sessions(limit: int = 50, p: Principal = Depends(authenticate)):
    rows = await _db_call(app.state.db.list_sessions, None if p.is_admin else p.key_id, max(1, min(limit, 200)))
    return {"data": [await _present(r) for r in rows]}


@app.get("/v1/sessions/{sid}")
async def get_session(sid: str, p: Principal = Depends(authenticate)):
    row = await _owned_session(sid, p)
    return await _present(row, timeline=True)


@app.get("/v1/sessions/{sid}/audit")
async def session_audit(sid: str, p: Principal = Depends(authenticate)):
    await _owned_session(sid, p)
    return {"data": await _db_call(app.state.db.audit_for_session, sid)}


@app.get("/v1/audit/verify")
async def verify_audit(p: Principal = Depends(authenticate)):
    if not p.is_admin:
        raise HTTPException(403, "Admin key required")
    return await _db_call(app.state.db.verify_chain)


@app.post("/v1/webhooks", status_code=201)
async def create_webhook(body: CreateWebhook, p: Principal = Depends(authenticate)):
    allowed = {"session.passed", "session.failed", "session.review", "*"}
    if not set(body.events) <= allowed:
        raise HTTPException(422, f"events must be a subset of {sorted(allowed)}")
    hook = await _db_call(app.state.db.add_webhook, p.key_id, body.url, body.events)
    await _db_call(app.state.db.audit, "webhook.created", None, p.key_id, {"id": hook["id"], "url": body.url})
    return hook  # the signing secret is shown only here


@app.get("/v1/webhooks")
async def list_webhooks(p: Principal = Depends(authenticate)):
    return {"data": await _db_call(app.state.db.list_webhooks, p.key_id)}


@app.delete("/v1/webhooks/{wid}")
async def delete_webhook(wid: str, p: Principal = Depends(authenticate)):
    if not await _db_call(app.state.db.delete_webhook, p.key_id, wid):
        raise HTTPException(404, "Webhook not found")
    return {"deleted": True}


@app.get("/v1/usage")
async def usage(p: Principal = Depends(authenticate)):
    return await _db_call(billing.summary, app.state.db, None if p.is_admin else p.key_id)


@app.post("/v1/keys", status_code=201)
async def create_key(body: CreateKey, p: Principal = Depends(authenticate)):
    if not p.is_admin:
        raise HTTPException(403, "Admin key required")
    kid, raw = await _db_call(app.state.db.create_key, body.name, "customer")
    await _db_call(app.state.db.audit, "key.created", None, p.key_id, {"new_key_id": kid, "name": body.name})
    return {"id": kid, "name": body.name, "api_key": raw, "note": "Store this key now; it cannot be shown again."}


# ---------------------------------------------------------------- WebSocket stream
def _client_view(res: dict[str, Any]) -> dict[str, Any]:
    """What the browser is allowed to see. Detailed scores help attackers tune a spoof, so in
    production (ZS_EXPOSE_SCORES_TO_CLIENT=0) the browser only learns coarse progress / verdict."""
    if EXPOSE_SCORES:
        return {k: res[k] for k in ("verdict", "probability", "modules", "flags", "elapsed_s", "latency_ms", "reasons")}
    return {"verdict": res["verdict"], "elapsed_s": res["elapsed_s"]}


@app.websocket("/ws")
async def stream(ws: WebSocket):
    await ws.accept()
    app_ = ws.app
    store, db = app_.state.store, app_.state.db
    tok = verify_client_token(ws.query_params.get("token", ""))
    if not tok:
        await ws.close(code=4401, reason="invalid or expired token")
        return
    if not await store.setnx(f"tok:{tok['n']}", settings.token_ttl_s + 60):
        await ws.close(code=4409, reason="token already used")  # replay protection
        return
    meta = await store.get_json(f"sess:{tok['sid']}")
    if not meta:
        await ws.close(code=4404, reason="unknown session")
        return

    sid, key_id = meta["id"], meta["key_id"]
    live = LiveSession(sid, key_id, meta.get("challenge"))
    app_.state.live[sid] = live
    finished = False
    last_analysis = 0.0
    window_start, window_n = time.time(), 0

    async def send(obj: dict[str, Any]) -> None:
        await ws.send_text(json.dumps(obj, separators=(",", ":")))

    async def run_analysis(final: bool) -> dict[str, Any]:
        res = await asyncio.to_thread(live.analyze, final)
        res["fingerprint"] = fp_mod.summarize(live.fingerprint) if live.fingerprint else None
        await store.set_json(f"live:{sid}", res, ttl=3600)
        point = {"t": res["elapsed_s"], "p": res["probability"], "v": res["verdict"],
                 **{n: m["score"] for n, m in res["modules"].items() if m["available"]}}
        await store.push_score(sid, point)
        return res

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            raw = msg.get("text") if msg.get("text") is not None else (msg.get("bytes") or b"").decode("utf-8", "ignore")
            if len(raw) > settings.ws_max_msg_bytes:
                await send({"type": "error", "message": "message too large"})
                await ws.close(code=1009)
                break
            now = time.time()
            if now - window_start >= 1.0:
                window_start, window_n = now, 0
            window_n += 1
            if window_n > settings.ws_max_msgs_per_s:
                await send({"type": "error", "message": "rate limit"})
                await ws.close(code=4429)
                break
            try:
                data = json.loads(raw)
                typ = data.get("type")
                if typ == "hello":
                    live.add_hello(data)
                    await send({"type": "ready", "challenge": meta.get("challenge"), "min_seconds": settings.min_seconds})
                elif typ == "video":
                    live.add_video(data)
                elif typ == "audio":
                    live.add_audio(data)
                elif typ == "challenge":
                    live.set_challenge_window(data)
                elif typ == "finish":
                    res = await run_analysis(final=True)
                    await _finalize(app_, sid, key_id, res)
                    finished = True
                    await send({"type": "final", **_client_view(res)})
                    await ws.close(code=1000)
                    break
                elif typ == "ping":
                    await send({"type": "pong"})
                else:
                    raise ProtocolError(f"unknown message type {typ!r}")
            except (ProtocolError, json.JSONDecodeError, AttributeError) as e:
                await send({"type": "error", "message": str(e) or "bad message"})
                continue

            if typ in ("video", "audio") and len(live.t) > 30 and time.time() - last_analysis >= 1.5:
                last_analysis = time.time()
                res = await run_analysis(final=False)
                await send({"type": "update", **_client_view(res)})
    except Exception as e:  # network drop etc.
        log.info("ws %s ended: %s", sid, type(e).__name__)
    finally:
        app_.state.live.pop(sid, None)
        if not finished:
            await _db_call(db.upsert_session, sid, status="aborted", finished_at=time.time())
            await _db_call(db.audit, "session.aborted", sid, key_id, {"elapsed_s": round(live.elapsed, 1)})


async def _finalize(app_: FastAPI, sid: str, key_id: str, res: dict[str, Any]) -> None:
    db = app_.state.db
    summary = {k: res[k] for k in ("modules", "flags", "reasons", "elapsed_s", "latency_ms", "fingerprint")}
    await _db_call(db.upsert_session, sid, status="complete", verdict=res["verdict"], probability=res["probability"],
                   finished_at=time.time(), summary=summary)
    await _db_call(db.audit, "session.completed", sid, key_id, {
        "verdict": res["verdict"], "probability": res["probability"],
        "modules": {n: m["score"] for n, m in res["modules"].items() if m["available"]},
        "flags": [f["code"] for f in res["flags"]], "elapsed_s": res["elapsed_s"]})
    await _db_call(db.record_usage, key_id, sid, res["verdict"])
    row = await _db_call(db.get_session, sid)
    if row:
        payload = {"id": sid, "external_id": row.get("external_id"), "verdict": res["verdict"],
                   "probability": res["probability"], "flags": [f["code"] for f in res["flags"]]}
        asyncio.create_task(deliver_event(db, key_id, payload))
