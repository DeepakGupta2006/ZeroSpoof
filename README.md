# ZeroSpoof: real-time deepfake & liveness firewall

ZeroSpoof answers one question for a video call, KYC or exam flow: **is the person on this call a real, live human, or a replay, a deepfake, a virtual camera or a cloned voice?**
It is an API plus a drop-in browser SDK. A session returns **pass**, **review** or **fail**, with per-module scores, threat flags and a tamper-evident audit trail.

> **Status: working reference implementation.** Every part runs and is tested end to end, but the detectors use classical signal processing and heuristics. Accuracy on real attacks is **not yet measured** (the included report uses synthetic data). Read [What is real and what is not](#what-is-real-and-what-is-not) before relying on it.

---

## What you get

| Piece | Folder | Tech |
|---|---|---|
| Inference + REST API | `inference/` | FastAPI, NumPy/SciPy, SQLAlchemy, Redis |
| Ingestion gateway | `gateway/` | Go WebSocket reverse proxy (rate limits, origin allow-list, keep-alive) |
| Browser SDK `<LivenessCheck />`, demo, dashboard | `web/` | Next.js 14, React, MediaPipe (WebAssembly) |
| Evaluation (APCER / BPCER / EER / latency), fusion training | `evaluation/` | Python |
| Tools: camera-free simulator, video scorer, webhook receiver | `tools/` | Python |
| Docs | `docs/` | API, models, privacy |
| Full stack | `docker-compose.yml` | Postgres 16, Redis 7 + the three services above |

### Architecture

```
 Browser (web/src/sdk)                    Go gateway :8080              Inference (FastAPI) :8000
 ┌───────────────────────────┐  WebSocket ┌──────────────┐  WebSocket  ┌──────────────────────────────┐
 │ camera + mic              │───────────►│ origin check │────────────►│ verify one-time token        │
 │ MediaPipe face tracking   │            │ rate limit   │             │ rPPG · moiré · depth · audio │
 │ skin-ROI colour means     │◄───────────│ size limit   │◄────────────│ device/stream checks         │
 │ 128px grey face crops     │  scores    └──────────────┘   scores    │ challenge-response           │
 │ 16 kHz audio chunks       │                                         │ fusion → pass/review/fail    │
 └───────────────────────────┘                                         └──────┬───────────┬───────────┘
        ▲ client token                                                        │           │
        │ (one-time, signed, 5 min)       Your backend ── POST /v1/sessions ──►│  Redis    │ Postgres
        └──────────────────────────────── (holds the secret API key)           │ live state│ audit chain, usage,
                                                                               │ rate limit│ webhooks, keys
                                       Webhooks (HMAC-signed) ◄────────────────┘
```

**Privacy by design:** the browser never uploads a video stream. It sends colour averages of three skin regions, small greyscale face crops, and audio. Nothing is stored after the session except scores, flags and the audit trail.

### The five threats and the counter for each

| Attack | Counter in ZeroSpoof | Where |
|---|---|---|
| Replay / screen | Moiré (FFT) and glare detection | `detectors/moire.py` |
| Photo / mask | Depth estimation (optional ONNX) and texture analysis | `detectors/depth.py` |
| Face-swap deepfake | rPPG pulse consistency across regions (POS method), challenge-response (head turn / blink) | `detectors/rppg.py`, `challenge.py` |
| Virtual-camera injection | Device-label and API-tamper checks, frame-timing regularity, loop detection | `detectors/fingerprint.py` |
| Voice clone / TTS | Spectral anti-spoofing features, or an AASIST/RawNet2 ONNX model if you provide one | `detectors/audio.py` |

All module scores (0 to 1) are combined by a small logistic fusion model (`fusion.py`) plus explicit, explainable rules. Modules without enough data are ignored rather than guessed.

---

## Quick start A: Docker (everything, one command)

Needs Docker Desktop (or Docker Engine + Compose v2).

```bash
cd ZeroSpoof
docker compose up --build
```

Open:

| URL | What |
|---|---|
| http://localhost:3000 | Live demo with your webcam |
| http://localhost:3000/dashboard | Dashboard: sessions, score timeline, alerts, fingerprints, audit trail, usage |
| http://localhost:8000/docs | Interactive API docs |
| http://localhost:8080/metrics | Gateway counters |

The first build takes a few minutes. Stop with `Ctrl+C`, remove data with `docker compose down -v`.

> The Docker files were written carefully but could not be built in the environment where this project was authored (no Docker daemon). If a build step fails, use Quick start B, and please check the error against the Dockerfile in that folder.

## Quick start B: run locally without Docker

Needs **Python 3.10+**, **Node 18+** (22 recommended) and, for the gateway, **Go 1.22+**. Redis and Postgres are optional: with no `REDIS_URL` / `DATABASE_URL` the service uses an in-memory store and a local SQLite file.

Open **three terminals** in the `ZeroSpoof` folder.

**Terminal 1: inference API**

```bash
cd inference
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn zerospoof.main:app --port 8000
```

Check it: open http://localhost:8000/health (you should see `"ok": true`).

**Terminal 2: web app (demo + dashboard)**

```bash
cd web
npm install
cp .env.example .env.local           # Windows: copy .env.example .env.local
npm run dev
```

**Terminal 3 (optional): Go gateway.** Without it, browsers connect straight to the inference service (the default `ZS_PUBLIC_WS_URL=ws://localhost:8000/ws`). To put the gateway in front, start the inference service with `ZS_PUBLIC_WS_URL=ws://localhost:8080/ws` and run:

```bash
cd gateway
go run .
```

Then:

1. Open http://localhost:3000, tick the consent box, press **Start liveness check**, and allow the camera. Keep your face in the oval for 10 seconds, then follow the head-turn instruction.
2. Open http://localhost:3000/dashboard in another tab to watch the session appear and update.

**Camera access only works on `http://localhost` or over HTTPS.** Opening the demo by LAN IP over plain HTTP will not give you a camera.

### No camera? Use the simulator

`tools/simulate_client.py` speaks the exact browser protocol with synthetic genuine and attack sessions, so you can see the dashboard fill up and test the gateway:

```bash
pip install httpx websockets numpy scipy     # already installed if you did Quick start B
python tools/simulate_client.py              # one session of every kind
python tools/simulate_client.py --kinds genuine,replay --repeat 3
```

Expected output (synthetic data):

```
kind         verdict      p  flags
genuine      pass      0.99
replay       fail      0.05  challenge_failed, moire_pattern, no_pulse
photo        fail      0.04  challenge_failed, moire_pattern, no_pulse
deepfake     review    0.55  no_pulse
virtual_cam  fail      0.15  looped_video, challenge_failed, virtual_camera, too_regular_timing
voice_clone  review    0.60  synthetic_voice
```

### Score a real video file

```bash
pip install "opencv-python-headless>=4.9,<5"
python tools/video_to_session.py me.mp4
python tools/video_to_session.py clip.mp4 --audio clip.wav
```

Use OpenCV 4.x (OpenCV 5 removed the face-detector class the tool uses). Offline mode has no browser fingerprint and no challenge, so those modules are skipped.

---

## Use it from your own backend

Your backend creates a session with the **secret API key**, and hands only the one-time client token to the browser.

```bash
curl -X POST http://localhost:8000/v1/sessions \
  -H "X-API-Key: zs_live_dev" -H "Content-Type: application/json" \
  -d '{"external_id":"application-1234"}'
```

```tsx
// Browser: copy web/src/sdk into your app, then
<LivenessCheck
  createSession={() => fetch("/api/session", { method: "POST" }).then(r => r.json())}
  onComplete={(r) => console.log(r.verdict)}
/>
```

The authoritative verdict goes to your **backend** by webhook (`session.passed`, `session.failed`, `session.review`) or by `GET /v1/sessions/{id}`. Never trust a verdict that only the browser reported. See [docs/API.md](docs/API.md) for every endpoint, the webhook signature check, and the WebSocket protocol.

Dev keys (only active while `ZS_ENV` is not `prod`): API key `zs_live_dev`, admin key `zs_admin_dev`.

---

## Configuration

Environment variables for the inference service (all optional in dev):

| Variable | Default | Meaning |
|---|---|---|
| `ZS_ENV` | `dev` | `prod` refuses default secrets, disables dev keys, and hides detailed scores from the browser |
| `DATABASE_URL` | local SQLite file | `postgresql://user:pass@host:5432/db` for production |
| `REDIS_URL` | empty (in-memory) | `redis://host:6379/0` |
| `ZS_ADMIN_KEY`, `ZS_DEV_API_KEY` | dev keys | bootstrap keys (dev only) |
| `ZS_TOKEN_SECRET` | dev value | signs the client tokens; **change it** |
| `ZS_PUBLIC_WS_URL` | `ws://localhost:8000/ws` | where browsers connect (the gateway in the full stack) |
| `ZS_PASS_T`, `ZS_FAIL_T` | `0.70`, `0.40` | verdict thresholds on the fused probability |
| `ZS_MIN_SECONDS` | `8` | minimum streamed time before a verdict |
| `ZS_DEMO_RELAX` | `0` | **Demo only.** `1` ignores an inconclusive pulse instead of capping the verdict at *review*. Weakens the face-swap defence; refused when `ZS_ENV=prod` |
| `ZS_MODELS_DIR` | `inference/models` | optional ONNX models and trained fusion weights |
| `ZS_BASE_PRICE`, `ZS_INCLUDED_CHECKS`, `ZS_PRICE_PER_1000` | `49`, `1000`, `25` | illustrative billing (validate with real prospects) |

Gateway: `UPSTREAM_WS`, `ZS_ALLOWED_ORIGINS`, `GATEWAY_LISTEN`, `GATEWAY_MAX_CONNS`, `GATEWAY_MAX_MSGS_PER_SEC`. Web: see `web/.env.example`.

## Tests

```bash
cd inference && pip install -r requirements-dev.txt && pytest        # 26 tests: detectors, API, WebSocket, audit chain, webhooks
cd web && npm run typecheck && npm run build
cd gateway && go vet ./...
python evaluation/evaluate.py --n 30 --out evaluation/report.md      # synthetic PAD report (APCER/BPCER/EER/latency)
```

## Evaluation

`evaluation/evaluate.py` reports what presentation-attack detection should report (ISO/IEC 30107-3 style): **APCER** per attack type, **BPCER**, **ACER**, **EER**, and decision latency, never accuracy alone. With `--scores` it evaluates scores from real datasets; `evaluation/train_fusion.py` trains the fusion weights, so you can train on one dataset and test on another. Step-by-step: [evaluation/README.md](evaluation/README.md).

## What is real and what is not

Honest status, so you can plan the next steps:

- **Real and tested:** the whole pipeline (browser capture, gateway, token auth, replay protection, rate limits, fusion, hash-chained audit log, signed webhooks, usage metering, dashboard), the rPPG (POS) estimator, the FFT moiré detector, stream and device checks, challenge-response.
- **Heuristic:** audio anti-spoofing ships as spectral features plus a small baseline. Real voice-clone detection needs a trained model (AASIST / RawNet2 on ASVspoof): see [docs/MODELS.md](docs/MODELS.md).
- **Optional:** the depth module needs an ONNX depth model (`python tools/get_models.py`). Without it, photo/mask defence relies on moiré, texture and the pulse check.
- **Not yet measured:** accuracy on real-world attacks. The bundled report is on synthetic data and only validates the plumbing and latency. A portfolio-grade claim needs the real datasets (FaceForensics++, Celeb-DF, Replay-Attack, OULU-NPU, UBFC-rPPG, ASVspoof) and an honest cross-dataset report.
- **Known limits:** rPPG degrades on heavily compressed video and in poor light, so it is one signal among several. Deepfake detection is an arms race; a real product needs continuous retraining. Client-side signals can be forged by someone who controls the browser, so the server also checks the signal itself.

## Before production

1. Set `ZS_ENV=prod` and strong values for `ZS_TOKEN_SECRET`, `ZS_ADMIN_KEY`, `POSTGRES_PASSWORD`. Create customer keys with `POST /v1/keys`.
2. Put TLS in front (camera access needs HTTPS) and use `wss://` for `ZS_PUBLIC_WS_URL`.
3. Set `DASHBOARD_PASSWORD`, `ZS_ALLOWED_ORIGINS` and `ZS_CORS_ORIGINS` to your real origins.
4. Self-host the MediaPipe WebAssembly and face model so no third party sees your users ([docs/PRIVACY.md](docs/PRIVACY.md)).
5. Run the real-data evaluation, train the fusion weights, and measure bias across skin tones, lighting and cameras.
6. Get legal review for GDPR, India's DPDP Act and Illinois BIPA: explicit consent, minimal storage, retention limits. ISO/IEC 30107 is the benchmark standard.

## Project layout

```
ZeroSpoof/
├── docker-compose.yml  .env.example
├── inference/    zerospoof/ (main.py engine.py fusion.py db.py store.py security.py webhooks.py billing.py
│                 synth.py detectors/{rppg,moire,depth,audio,fingerprint,challenge}.py)  tests/  Dockerfile
├── gateway/      main.go  Dockerfile
├── web/          src/sdk/ (LivenessCheck.tsx capture.ts face.ts fingerprint.ts)  src/app/ (demo, dashboard, API routes)
├── evaluation/   evaluate.py  train_fusion.py  report.md  README.md
├── tools/        simulate_client.py  video_to_session.py  webhook_receiver.py  get_models.py  train_audio_baseline.py
└── docs/         API.md  MODELS.md  PRIVACY.md
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser says camera unavailable | Use `http://localhost:3000` (not a LAN IP) or HTTPS; allow camera in the browser |
| "Could not reach the ZeroSpoof server" | Inference not running, or `ZS_PUBLIC_WS_URL` points at a gateway that is not running |
| Every check says *fail* | Look at the flags under the preview (demo page) or **Threat alerts** (dashboard). Most common: you did not turn your head far enough or in time (turn clearly, about 30°, as soon as the instruction appears), or the face tracker could not load (open the browser console: `face tracker unavailable`), which now gives *review* instead of *fail*. Dim light also weakens the pulse signal |
| Face tracker never loads | MediaPipe files come from a CDN: check your network, or self-host them. Without it the head-turn challenge cannot run, so the session ends in *review*, never *pass* |
| Dashboard shows "Cannot reach the ZeroSpoof API" | Start Terminal 1; check `ZEROSPOOF_API_URL` in `web/.env.local` |
| `pip install` fails on `psycopg` | Only needed for Postgres. Remove that line for SQLite-only dev |
| `video_to_session.py` fails with `CascadeClassifier` | Install OpenCV 4.x: `pip install "opencv-python-headless<5"` |
