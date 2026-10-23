# ZeroSpoof API

Interactive docs are served by the inference service at `/docs` (OpenAPI). This page covers the flow and the parts OpenAPI cannot show.

## Flow

```
Your backend                    ZeroSpoof                         Browser
    │  POST /v1/sessions  ───────►│                                   │
    │◄── session_id, client_token │                                   │
    │  hand client_token + ws_url ───────────────────────────────────►│
    │                             │◄── WebSocket /ws?token=...  ──────│
    │                             │      hello / video / audio ...    │
    │◄── webhook session.* ───────│── final verdict ─────────────────►│
    │  GET /v1/sessions/{id}      │                                   │
```

The API key stays on your server. The browser only ever holds the one-time client token (HMAC-signed, expires after `ZS_TOKEN_TTL` seconds, single use).

## Authentication

Send `X-API-Key: <key>` on every REST call. Keys are stored hashed. Each key is rate limited per minute (`ZS_API_RATE_PER_MIN`).

Optional replay protection for POST/DELETE: send `X-ZS-Nonce` (unique per request) and `X-ZS-Timestamp` (unix seconds, within 5 minutes). Set `ZS_REQUIRE_NONCE=1` to make them mandatory.

## REST endpoints

| Method and path | Who | Purpose |
|---|---|---|
| `POST /v1/sessions` | customer | Create a session. Body: `{"external_id": "your-ref", "challenge": true}`. Returns `session_id`, `client_token`, `ws_url`, `challenge`, `min_seconds`, `expires_in` |
| `GET /v1/sessions` | customer | Recent sessions (`?limit=50`) |
| `GET /v1/sessions/{id}` | customer | Verdict, probability, per-module scores, flags, reasons, fingerprint summary, score timeline |
| `GET /v1/sessions/{id}/audit` | customer | Hash-chained audit entries for the session |
| `POST /v1/webhooks` | customer | Register `{"url": "https://...", "events": ["session.failed","session.review"]}`. The signing secret is returned **once** |
| `GET /v1/webhooks`, `DELETE /v1/webhooks/{id}` | customer | List and remove |
| `GET /v1/usage` | customer | Checks this month and an estimated bill |
| `POST /v1/keys` | admin | Create a customer API key (shown once) |
| `GET /v1/audit/verify` | admin | Recompute the whole hash chain: `{"ok": true, "checked": N, "first_bad_id": null}` |
| `GET /health` | none | Liveness probe |

A session's `verdict` is `pending` (still collecting), `pass`, `review` or `fail`. Treat `review` as "send to a human or ask for another attempt".

## Webhooks

Events: `session.passed`, `session.failed`, `session.review` (or `*`). Delivery is a JSON POST with 3 attempts (0 s, 2 s, 8 s). Every attempt is written to the audit log.

```json
{ "id": "evt_9f2c...", "type": "session.failed", "created": 1760000000,
  "data": { "id": "ses_...", "external_id": "application-1234", "verdict": "fail",
            "probability": 0.05, "flags": ["moire_pattern", "no_pulse"] } }
```

Header `X-ZeroSpoof-Signature: t=<unix>,v1=<hex>` where `v1 = HMAC_SHA256(secret, "<t>." + raw_body)`. Verify it against the **raw** body and reject timestamps older than 5 minutes. A reference verifier is in `tools/webhook_receiver.py`:

```bash
python tools/webhook_receiver.py --secret whsec_xxx --port 9001
curl -X POST localhost:8000/v1/webhooks -H "X-API-Key: zs_live_dev" -H "Content-Type: application/json" \
     -d '{"url":"http://localhost:9001/hook","events":["*"]}'
```

## WebSocket protocol (what the SDK speaks)

Connect to `ws_url?token=<client_token>`. All messages are JSON text.

| Direction | Message |
|---|---|
| client → server | `{"type":"hello","fingerprint":{...}}` (once, first) |
| client → server | `{"type":"video","frames":[{"t":ms,"rois":[[r,g,b]x3],"yaw":deg,"blink":0..1}],"patches":[{"t":ms,"data":"<base64 of 128x128 uint8 grey>"}]}` |
| client → server | `{"type":"audio","sr":16000,"pcm":"<base64 int16 little-endian mono>"}` |
| client → server | `{"type":"challenge","nonce":"...","start":ms,"end":ms}` (when the challenge window closes) |
| client → server | `{"type":"finish"}` → server answers with `final` and closes |
| client → server | `{"type":"ping"}` → `{"type":"pong"}` |
| server → client | `ready`, `update` (about every 1.5 s), `final`, `error` |

`rois` are the mean RGB (0..255) of forehead, left cheek, right cheek. In production (`ZS_ENV=prod`) the browser only receives `{verdict, elapsed_s}`, because detailed scores help an attacker tune a spoof. Your backend still gets everything through REST.

Close codes: `4401` bad or expired token, `4409` token already used, `4404` unknown session, `4429` rate limit, `1009` message too large.

## Module names in responses

`rppg` (pulse), `moire` (screen/print patterns and glare), `depth` (flatness, optional model), `audio` (synthetic voice), `fingerprint` (device and tampering hints), `stream` (frame timing, loops, frozen frames), `challenge` (head turn / blink). Each has `score` (1 = looks live), `confidence`, `available`, `details`. Modules with `available: false` are ignored by the fusion step.
