# Privacy and compliance notes

Biometric data is sensitive. ZeroSpoof is designed for consent and minimal storage, but **compliance is your responsibility**: this page is engineering guidance, not legal advice. Have a lawyer review GDPR, India's DPDP Act and Illinois BIPA (and any other law that applies to your users).

## What leaves the device

| Data | Sent? | Notes |
|---|---|---|
| Raw video | **No** | Never streamed or uploaded |
| Mean colour of 3 skin regions per frame | Yes | The rPPG signal |
| 128x128 greyscale face crop, about 5 per second | Yes | Needed for moiré / screen / print analysis |
| Microphone audio (16 kHz mono) | Yes, optional | `options={{ audio: false }}` turns it off |
| Device fingerprint (camera label, UA, platform, timezone, canvas hash) | Yes | Used only to spot virtual cameras and tampering; raw device IDs are not sent |

## What the server keeps

After a session ends: the verdict, per-module scores, flags, a score timeline, a small fingerprint summary (hashed device id, camera label, resolution) and hash-chained audit entries. **Frames, crops and audio are held in memory for the session only and are not written to disk.**

## Built-in controls

- Explicit consent checkbox in the widget before the camera starts.
- One-time, short-lived, signed client tokens. The API key never reaches the browser.
- Audit log is append-only (database trigger) and hash-chained, so edits are detectable (`GET /v1/audit/verify`).
- In `ZS_ENV=prod` the browser only receives the verdict, not detailed scores.

## Still to do on your side

- Retention policy: schedule deletion of old sessions and audit rows to match your legal basis. (The audit chain is deliberately tamper-evident, so plan pruning with checkpoints.)
- Consent text, privacy notice, and a non-biometric fallback for users who decline.
- Data-processing agreements with customers if you operate this as a service.
- Bias testing across skin tones, lighting and cameras, with the results documented.

## Self-host the face tracker

By default the widget downloads the MediaPipe WebAssembly and face model from public CDNs (jsdelivr, Google storage), so those hosts see the user's IP. To avoid this:

1. `npm pack @mediapipe/tasks-vision@0.10.14`, extract it, and copy its `wasm/` folder to `web/public/mediapipe/wasm`.
2. Download `face_landmarker.task` and save it as `web/public/mediapipe/face_landmarker.task`.
3. Set `NEXT_PUBLIC_MEDIAPIPE_WASM_URL=/mediapipe/wasm` and `NEXT_PUBLIC_FACE_MODEL_URL=/mediapipe/face_landmarker.task`, then rebuild (these values are baked in at build time).
