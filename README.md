# ZeroSpoof: Real-Time Deepfake & Liveness Firewall

**Is the person on this call a real, live human, or a replay, a deepfake, a virtual camera or a cloned voice?**

ZeroSpoof is an API plus a drop-in browser SDK that a company adds to its video call, KYC or exam flow. A short camera and microphone check returns **pass**, **review** or **fail**, together with per-detector scores, threat flags and a tamper-evident audit trail.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Go](https://img.shields.io/badge/Go-1.22-00ADD8?logo=go&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)

> **Project status: working prototype.** The whole pipeline runs and is tested end to end, but the detectors use classical signal processing and heuristics. **Accuracy against real attacks has not been measured yet**: the bundled evaluation report uses generated data and only validates the plumbing and latency. See [Current status and limitations](#current-status-and-limitations) before relying on it.

---

## Table of contents

1. [Why ZeroSpoof](#why-zerospoof)
2. [Features](#features)
3. [Screenshots](#screenshots)
4. [How it works](#how-it-works)
5. [Tech stack](#tech-stack)
6. [Architecture](#architecture)
7. [Project structure](#project-structure)
8. [Getting started](#getting-started)
9. [Using ZeroSpoof from your own backend](#using-zerospoof-from-your-own-backend)
10. [Configuration](#configuration)
11. [Testing](#testing)
12. [Evaluation](#evaluation)
13. [Security and privacy](#security-and-privacy)
14. [Current status and limitations](#current-status-and-limitations)
15. [Roadmap](#roadmap)
16. [Troubleshooting](#troubleshooting)
17. [License and author](#license-and-author)

---

## Why ZeroSpoof

Remote hiring platforms, fintech onboarding apps, online exam portals and call centers lose money to identity spoofing. Attackers use five main techniques:

| Attack | What the attacker does | ZeroSpoof counter |
|---|---|---|
| **Replay / screen** | Holds a phone or monitor showing a real person's video | Moiré (FFT) and screen-glare detection |
| **Photo / mask** | Shows a printed photo or wears a mask | Texture analysis, optional depth model |
| **Face-swap deepfake** | Uses a real-time face filter | Pulse (rPPG) consistency, random challenge-response |
| **Virtual camera** | Feeds fake video through OBS or similar | Device and driver checks, frame-timing analysis, loop detection |
| **Voice clone / TTS** | Plays a synthetic voice | Audio anti-spoofing features (model-ready) |

---

## Features

**Detection**
- **Pulse detection (rPPG).** Extracts a heartbeat signal from skin colour with the POS method and checks that it is steady and consistent across forehead and cheeks. Motion-aware: it measures the pulse only while the head is still.
- **Moiré and glare detection.** FFT analysis of face crops to catch a camera filming a screen or a printed halftone.
- **Depth and texture check.** Optional monocular depth model (ONNX) to confirm the face is 3D.
- **Challenge-response.** A random instruction (turn left, turn right, blink twice) that a pre-recorded video cannot answer.
- **Virtual-camera and tamper detection.** Camera labels, browser API tampering, headless or automated browsers, too-regular frame timing, frozen frames and looped clips.
- **Voice anti-spoofing.** Spectral features for synthetic speech, with a drop-in slot for an AASIST or RawNet2 ONNX model.
- **Transparent score fusion.** A small logistic model plus explicit rules. Every verdict lists its reasons.

**Product and platform**
- Drop-in `<LivenessCheck />` React component with an explicit consent step.
- Browser-side preprocessing with WebAssembly (MediaPipe): raw video never leaves the device.
- Go WebSocket gateway for many concurrent sessions, with origin allow-list, rate limits and keep-alive.
- Live **Next.js dashboard**: sessions, score timelines, threat alerts, device fingerprints, audit trail, usage.
- **Signed webhooks** (`session.passed`, `session.failed`, `session.review`) with retries.
- **API keys, rate limiting and replay protection**, plus usage metering per 1,000 checks.
- **Hash-chained, append-only audit log** (verifiable through the API).
- Camera-free **simulator** and an **offline video scorer** for testing.
- Evaluation tooling for **APCER, BPCER, ACER, EER and latency**, plus fusion training.
- Docker Compose for the full stack.

---

## Screenshots

> Add your own images to `docs/images/` and keep these lines. The files are not included in the repository by default.

| Live demo | Dashboard |
|---|---|
| ![Demo](docs/images/demo.png) | ![Dashboard](docs/images/dashboard.png) |

---

## How it works

### 1. A check, step by step

1. Your backend asks ZeroSpoof for a session using a **secret API key** and receives a **one-time client token**.
2. The browser connects over WebSocket with that token (signed, short-lived, single use).
3. The user looks at the camera and holds still for about 10 seconds, then performs a random challenge.
4. The browser tracks the face locally and sends only: the mean colour of three skin regions per frame, small greyscale face crops (about 5 per second), and 16 kHz audio chunks.
5. The server runs all detectors every ~1.5 seconds and fuses their scores.
6. At the end, the final verdict is returned to the browser and delivered to your backend by **webhook** or `GET /v1/sessions/{id}`.

### 2. Detectors

| Module | Idea | File |
|---|---|---|
| `rppg` | Pulse from skin colour (POS), steadiness over time, coherence across regions | `inference/zerospoof/detectors/rppg.py` |
| `moire` | FFT peaks from screen or print patterns, glare blobs | `detectors/moire.py` |
| `depth` | Is the face 3D? (needs optional ONNX model) | `detectors/depth.py` |
| `challenge` | Did the user perform the random head turn or blink? | `detectors/challenge.py` |
| `fingerprint` / `stream` | Virtual camera labels, tampered APIs, frame timing, loops | `detectors/fingerprint.py` |
| `audio` | Synthetic-speech cues, or an ONNX model if provided | `detectors/audio.py` |

Every module returns a **score** (1 = looks live) and a **confidence**. Modules without enough data are ignored instead of guessed.

### 3. Score fusion and verdict

`fusion.py` combines the module scores with a small logistic model and applies explainable rules:

- Strong evidence of an attack from a trusted module (for example a failed challenge or a detected virtual camera) caps the probability and forces **fail**.
- A synthetic-voice hint alone never fails a session; it forces **review**.
- A **pass** needs both a screen/print check and at least one liveness cue.
- If the browser could not load face tracking, the challenge cannot be judged, so the session ends in **review**, never **pass**.

| Fused probability | Verdict | Suggested action |
|---|---|---|
| 0.70 or higher | **pass** | Let the user continue |
| 0.40 to 0.70 | **review** | Human check, or ask the user to retry |
| below 0.40 | **fail** | Block |

Thresholds are configurable (`ZS_PASS_T`, `ZS_FAIL_T`).

---

## Tech stack

| Layer | Technology |
|---|---|
| **Frontend / SDK** | Next.js 14, React 18, TypeScript, MediaPipe Tasks Vision (WebAssembly), Web Audio (AudioWorklet), WebSocket |
| **Ingestion gateway** | Go 1.22, `gorilla/websocket` |
| **Inference service** | Python 3.10+, FastAPI, Uvicorn, NumPy, SciPy, Pydantic |
| **State and storage** | Redis (live sessions, rate limits, one-time tokens), PostgreSQL 16 (audit log, keys, webhooks, usage), SQLite for zero-setup local dev, SQLAlchemy 2.0, psycopg 3 |
| **Optional models** | ONNX Runtime (depth model, AASIST/RawNet2 audio model) |
| **Security** | HMAC-signed tokens and webhooks, hashed API keys, rate limiting, replay protection, hash-chained audit log |
| **Tooling** | pytest, Docker and Docker Compose, TypeScript type checking |

---

## Architecture

```
 Browser (web/src/sdk)                   Go gateway :8080              Inference (FastAPI) :8000
 ┌───────────────────────────┐  WebSocket ┌──────────────┐  WebSocket  ┌──────────────────────────────┐
 │ camera + microphone       │───────────►│ origin check │────────────►│ verify one-time token        │
 │ MediaPipe face tracking   │            │ rate limit   │             │ rPPG · moire · depth · audio │
 │ skin-ROI colour means     │◄───────────│ size limit   │◄────────────│ device and stream checks     │
 │ 128px greyscale crops     │   scores   └──────────────┘   scores    │ challenge-response           │
 │ 16 kHz audio chunks       │                                         │ fusion → pass / review / fail│
 └───────────────────────────┘                                         └──────┬───────────┬───────────┘
        ▲ client token                                                        │           │
        │ (one-time, signed)            Your backend ── POST /v1/sessions ───►│  Redis    │ PostgreSQL
        └──────────────────────────────── (holds the secret API key)           │ live state│ audit chain, usage,
                                                                               │ rate limit│ webhooks, API keys
                                      Webhooks (HMAC-signed) ◄─────────────────┘
```

The Go gateway is optional in local development: the browser can connect straight to the inference service.

---

## Project structure

```
ZeroSpoof/
├── README.md
├── docker-compose.yml          # postgres + redis + inference + gateway + web
├── .env.example                # copy to .env for Docker Compose
├── .gitignore
│
├── inference/                  # Python: detectors, fusion, REST API, WebSocket ingest
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── models/                 # optional ONNX models and trained fusion weights
│   ├── zerospoof/
│   │   ├── main.py             # FastAPI app: REST + /ws
│   │   ├── engine.py           # LiveSession: buffers a stream, runs all detectors
│   │   ├── fusion.py           # score fusion and verdict rules
│   │   ├── config.py           # settings from environment variables
│   │   ├── security.py         # API keys, client tokens, rate limit, webhook signing
│   │   ├── db.py               # SQLAlchemy: keys, sessions, audit chain, usage
│   │   ├── store.py            # Redis or in-memory session state
│   │   ├── webhooks.py         # signed delivery with retries
│   │   ├── billing.py          # usage metering and illustrative pricing
│   │   ├── synth.py            # synthetic genuine and attack sessions
│   │   ├── simclient.py        # turns synthetic sessions into WebSocket messages
│   │   └── detectors/
│   │       ├── rppg.py         # pulse (POS), motion gating
│   │       ├── moire.py        # screen and print patterns, glare
│   │       ├── depth.py        # optional depth model
│   │       ├── audio.py        # synthetic-voice cues, ONNX slot
│   │       ├── challenge.py    # head turn / blink verification
│   │       └── fingerprint.py  # virtual camera, tamper, timing, loops
│   └── tests/                  # detectors, API, WebSocket, audit chain, webhooks
│
├── gateway/                    # Go WebSocket reverse proxy
│   ├── main.go
│   ├── go.mod
│   └── Dockerfile
│
├── web/                        # Next.js 14 app
│   ├── Dockerfile
│   ├── package.json
│   ├── public/zerospoof-audio-worklet.js
│   └── src/
│       ├── sdk/                # the drop-in SDK
│       │   ├── LivenessCheck.tsx   # <LivenessCheck /> component
│       │   ├── capture.ts          # camera, audio, WebSocket, phases
│       │   ├── face.ts             # MediaPipe face tracking
│       │   ├── fingerprint.ts      # device fingerprint
│       │   ├── types.ts
│       │   └── index.ts
│       ├── app/
│       │   ├── page.tsx            # live demo
│       │   ├── dashboard/page.tsx  # dashboard
│       │   ├── api/                # server-side routes that hold the API keys
│       │   ├── layout.tsx
│       │   └── globals.css
│       ├── lib/zs.ts               # server-side helper for calling ZeroSpoof
│       └── middleware.ts           # optional dashboard password
│
├── evaluation/                 # APCER / BPCER / EER / latency, fusion training
│   ├── evaluate.py
│   ├── train_fusion.py
│   ├── report.md
│   └── README.md
│
├── tools/
│   ├── simulate_client.py      # act as the browser, no camera needed
│   ├── video_to_session.py     # score a real video file offline
│   ├── webhook_receiver.py     # example signed-webhook receiver
│   ├── train_audio_baseline.py # train a baseline audio model
│   └── get_models.py           # fetch the optional depth model
│
├── scripts/dev.sh              # one-command local dev (macOS / Linux)
└── docs/
    ├── API.md                  # endpoints, webhooks, WebSocket protocol
    ├── MODELS.md               # how to add and train models
    └── PRIVACY.md              # data handling and compliance notes
```

---

## Getting started

You need **Python 3.10+** and **Node.js 18+** (22 recommended). Go 1.22+ is only needed for the optional gateway. Redis and PostgreSQL are optional: without them the service uses an in-memory store and a local SQLite file.

Camera access only works on `http://localhost` or over HTTPS.

### Option A: Windows (PowerShell), two windows

**Window 1: backend**

```powershell
cd inference
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn zerospoof.main:app --port 8000
```

If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first. Check http://localhost:8000/health shows `"ok": true`.

**Window 2: web app**

```powershell
cd web
npm install
copy .env.example .env.local
npm run dev
```

### Option B: macOS / Linux

```bash
bash scripts/dev.sh
```

This creates the virtual environment, installs dependencies and starts both services.

### Option C: Docker (everything)

```bash
docker compose up --build
```

> The Docker setup was written carefully but has not been built or tested in the authoring environment. If a build step fails, use Option A or B.

### Then

| URL | What |
|---|---|
| http://localhost:3000 | Live demo with your webcam |
| http://localhost:3000/dashboard | Dashboard |
| http://localhost:8000/docs | Interactive API docs |
| http://localhost:8080/metrics | Gateway counters (only if the gateway runs) |

### Run a check

1. Open http://localhost:3000, tick the consent box and press **Start liveness check**.
2. Allow the camera and microphone.
3. Put your face in the oval, in bright light from in front of you, and hold still for about 10 seconds.
4. When the instruction appears, perform it clearly (for a head turn, about 30 degrees or more).
5. Watch the result, and open the dashboard in another tab to see the session.

### No camera? Use the simulator

```bash
pip install httpx websockets numpy scipy
python tools/simulate_client.py
python tools/simulate_client.py --kinds genuine,replay --repeat 3
```

### Optional: put the Go gateway in front

Start the inference service with `ZS_PUBLIC_WS_URL=ws://localhost:8080/ws`, then:

```bash
cd gateway
go run .
```

### Score a real video file

```bash
pip install "opencv-python-headless>=4.9,<5"
python tools/video_to_session.py me.mp4
```

Use OpenCV 4.x; OpenCV 5 removed the face-detector class this tool uses.

---

## Using ZeroSpoof from your own backend

Your backend creates a session with the **secret API key** and hands only the one-time client token to the browser.

```bash
curl -X POST http://localhost:8000/v1/sessions \
  -H "X-API-Key: zs_live_dev" -H "Content-Type: application/json" \
  -d '{"external_id":"application-1234"}'
```

```tsx
<LivenessCheck
  createSession={() => fetch("/api/session", { method: "POST" }).then(r => r.json())}
  onComplete={(r) => console.log(r.verdict)}
/>
```

Never trust a verdict that only the browser reported: read the final verdict from a webhook or from `GET /v1/sessions/{id}`.

| Endpoint | Purpose |
|---|---|
| `POST /v1/sessions` | Create a session and get a client token |
| `GET /v1/sessions`, `GET /v1/sessions/{id}` | List sessions, or get one with scores, flags and timeline |
| `GET /v1/sessions/{id}/audit` | Audit entries for a session |
| `POST/GET/DELETE /v1/webhooks` | Manage signed webhooks |
| `GET /v1/usage` | Checks this month and estimated bill |
| `POST /v1/keys` (admin) | Create a customer API key |
| `GET /v1/audit/verify` (admin) | Verify the whole audit hash chain |
| `GET /health` | Health check |
| `WS /ws?token=...` | Streaming endpoint used by the SDK |

Full details, the webhook signature check and the WebSocket protocol are in [docs/API.md](docs/API.md).

---

## Configuration

Environment variables for the inference service (all optional in development):

| Variable | Default | Meaning |
|---|---|---|
| `ZS_ENV` | `dev` | `prod` refuses default secrets, disables dev keys, hides detailed scores from the browser, and refuses demo mode |
| `DATABASE_URL` | local SQLite file | e.g. `postgresql://user:pass@host:5432/db` |
| `REDIS_URL` | empty (in-memory) | e.g. `redis://host:6379/0` |
| `ZS_ADMIN_KEY`, `ZS_DEV_API_KEY` | public dev keys | Bootstrap keys (development only) |
| `ZS_TOKEN_SECRET` | dev value | Signs client tokens. **Change it** |
| `ZS_PUBLIC_WS_URL` | `ws://localhost:8000/ws` | Where browsers connect |
| `ZS_PASS_T`, `ZS_FAIL_T` | `0.70`, `0.40` | Verdict thresholds |
| `ZS_MIN_SECONDS` | `8` | Minimum streamed time before a verdict |
| `ZS_MODELS_DIR` | `inference/models` | Optional ONNX models and trained fusion weights |
| `ZS_DEMO_RELAX` | `0` | **Demo only.** Ignores an inconclusive pulse instead of capping the verdict at *review*. Weakens the face-swap defence |
| `ZS_BASE_PRICE`, `ZS_INCLUDED_CHECKS`, `ZS_PRICE_PER_1000` | `49`, `1000`, `25` | Illustrative billing |

Gateway: `UPSTREAM_WS`, `ZS_ALLOWED_ORIGINS`, `GATEWAY_LISTEN`, `GATEWAY_MAX_CONNS`, `GATEWAY_MAX_MSGS_PER_SEC`.
Web: see `web/.env.example`.

**Dev keys** `zs_live_dev` (API) and `zs_admin_dev` (admin) are public. They only work while `ZS_ENV` is not `prod`.

---

## Testing

```bash
cd inference && pip install -r requirements-dev.txt && python -m pytest   # backend tests
cd web && npm run typecheck && npm run build                              # web
cd gateway && go vet ./...                                                # gateway
python evaluation/evaluate.py --n 30 --out evaluation/report.md           # synthetic PAD report
```

The backend tests cover the detectors, the REST API, the WebSocket flow, replay protection, the audit hash chain, webhooks, the face-tracker-unavailable path and the demo switch.

---

## Evaluation

`evaluation/evaluate.py` reports what presentation-attack detection should report (ISO/IEC 30107-3 style), not accuracy alone:

- **APCER** per attack type (attacks wrongly accepted)
- **BPCER** (genuine users wrongly rejected)
- **ACER**, **EER**, and **decision latency**

The bundled report is on **generated data**. It validates the pipeline and measures speed (about 100 ms per full-session decision on one CPU thread), but says nothing about real-world accuracy. To measure real accuracy:

1. Get public datasets (licences and request forms apply): Replay-Attack, CASIA-FASD, OULU-NPU (screen and print), FaceForensics++, Celeb-DF (deepfakes), UBFC-rPPG, PURE (pulse), ASVspoof, WaveFake, In-the-Wild (audio).
2. Score each clip with `tools/video_to_session.py`.
3. Report with `evaluate.py --scores`, and train on one dataset and test on another (cross-dataset).

Step-by-step instructions are in [evaluation/README.md](evaluation/README.md). Do not redistribute the datasets.

---

## Security and privacy

- **Raw video is never uploaded.** The browser sends skin-colour averages, small greyscale face crops and audio.
- Explicit **consent** step before the camera starts; audio can be turned off.
- Nothing is kept after a session except scores, flags, a small device summary and audit entries.
- Client tokens are signed, short-lived and single use; API keys are stored hashed; webhooks are HMAC-signed.
- The audit log is append-only and hash-chained, so edits are detectable.
- In `ZS_ENV=prod` the browser only receives the verdict, not detailed scores, because scores help attackers tune a spoof.

Biometric data is sensitive. Have a lawyer review GDPR, India's DPDP Act and Illinois BIPA before any real deployment. More in [docs/PRIVACY.md](docs/PRIVACY.md).

---

## Current status and limitations

**Working and tested:** the full pipeline (browser capture, gateway, token auth, replay protection, rate limits, fusion, hash-chained audit log, signed webhooks, usage metering, dashboard), the rPPG estimator, the FFT moiré detector, stream and device checks, and challenge-response.

**Known limitations:**

- **No real-world accuracy numbers yet.** It has not been evaluated on real attacks or public datasets.
- **Pulse detection is weak on ordinary webcams.** It depends on lighting and compression, and often returns no reliable pulse. Demo mode (`ZS_DEMO_RELAX=1`) ignores that case; do not describe results obtained with it as pulse-verified.
- **Voice and deepfake detection are heuristic.** A real voice detector needs a trained model such as AASIST on ASVspoof.
- **Photo and mask defence is partial** without the optional depth model.
- **A live face-swap attacker can perform the challenge.** Deepfake detection is an arms race and needs continuous retraining.
- **Client-side signals can be forged** by someone who controls the browser, so the server also checks the signals themselves.
- **Docker setup is untested.**

---

## Roadmap

- [ ] Evaluate on real datasets and publish APCER / BPCER / EER with a cross-dataset table
- [ ] Train an audio anti-spoofing model (AASIST / RawNet2) and export to ONNX
- [ ] Add a deep rPPG model (PhysNet or TS-CAN)
- [ ] Test against compression (Zoom/Meet-like) and measure bias across skin tones and lighting
- [ ] Add blending-artifact detection for face swaps
- [ ] Batch dataset-scoring tool
- [ ] Self-host the MediaPipe files, and add production deployment guides
- [ ] GitHub Actions CI

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `requirements.txt` not found | Run `cd inference` first |
| Camera unavailable | Use `http://localhost:3000` (not an IP address) or HTTPS, and allow the camera |
| "Could not reach the ZeroSpoof server" | Backend is not running, or `ZS_PUBLIC_WS_URL` points to a gateway that is not running |
| Result is always **fail** | Check the flags under the preview or in the dashboard. Usually the head turn was too small or too late |
| Result is **review** with "no reliable pulse" | Use bright light in front of you and stay still for the first 10 seconds |
| Face tracker never loads | The MediaPipe files download from a CDN; check your network. The session then ends in *review* |
| Dashboard says "Cannot reach the ZeroSpoof API" | Start the backend; check `ZEROSPOOF_API_URL` in `web/.env.local` |
| `video_to_session.py` fails on `CascadeClassifier` | Install OpenCV 4.x: `pip install "opencv-python-headless<5"` |
| Red crosses on the folder in File Explorer | OneDrive sync problem with `.venv` or `node_modules`; keep the project outside OneDrive |

---

## License and author

Released under the **MIT License** (add a `LICENSE` file to the repository root to activate it).

Built by **Deepak**.

**References:** Wang et al., "Algorithmic Principles of Remote PPG" (POS method, 2017); ISO/IEC 30107-3 (presentation attack detection testing); datasets and models as listed above, each under its own licence.
