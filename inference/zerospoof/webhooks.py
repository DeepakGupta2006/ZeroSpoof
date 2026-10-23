"""Webhook delivery: signed JSON, 3 attempts with backoff, every attempt written to the audit log."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

from .security import sign_webhook

log = logging.getLogger("zerospoof.webhooks")
EVENT_FOR_VERDICT = {"pass": "session.passed", "fail": "session.failed", "review": "session.review"}
BACKOFF = (0, 2, 8)  # seconds before each attempt


async def deliver_event(db, key_id: str, session: dict[str, Any]) -> None:
    event = EVENT_FOR_VERDICT.get(session["verdict"])
    if not event:
        return
    hooks = await asyncio.to_thread(db.list_webhooks, key_id, True)
    hooks = [h for h in hooks if event in h["events"] or "*" in h["events"]]
    if not hooks:
        return
    await asyncio.gather(*(_send(db, key_id, h, event, session) for h in hooks))


async def _send(db, key_id: str, hook: dict[str, Any], event: str, session: dict[str, Any]) -> None:
    payload = {"id": "evt_" + uuid.uuid4().hex[:16], "type": event, "created": int(time.time()), "data": session}
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    for attempt, wait in enumerate(BACKOFF, start=1):
        if wait:
            await asyncio.sleep(wait)
        ts = int(time.time())
        headers = {"Content-Type": "application/json", "X-ZeroSpoof-Signature": sign_webhook(hook["secret"], ts, body),
                   "X-ZeroSpoof-Event": event, "User-Agent": "ZeroSpoof-Webhook/0.1"}
        status: int | str
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(hook["url"], content=body, headers=headers)
            status = r.status_code
            ok = 200 <= r.status_code < 300
        except Exception as e:
            status, ok = type(e).__name__, False
        await asyncio.to_thread(db.audit, "webhook.delivery", session["id"], key_id,
                                {"webhook": hook["id"], "event": event, "attempt": attempt, "status": status, "ok": ok})
        if ok:
            return
    log.warning("webhook %s gave up after %d attempts", hook["id"], len(BACKOFF))
