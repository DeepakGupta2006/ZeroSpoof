#!/usr/bin/env python3
"""Run a REAL video file (and optional WAV) through the ZeroSpoof pipeline, offline.

Use it to try your own recordings, or to score dataset videos (UBFC-rPPG, Replay-Attack, CASIA-FASD,
FaceForensics++ ...) for evaluation/evaluate.py and evaluation/train_fusion.py.

    pip install opencv-python-headless numpy scipy
    python tools/video_to_session.py me.mp4
    python tools/video_to_session.py replay_clip.mp4 --label attack --attack-type replay --scores-csv scores.csv
    python tools/video_to_session.py clip.mp4 --audio clip.wav --label bonafide --modules-csv modules.csv

What it can and cannot do offline:
  + rPPG (three face regions), moire/glare, loop & timing checks, audio
  - no browser fingerprint (no 'hello'), no challenge-response (no landmarks): those modules are skipped
Face detection uses OpenCV's Haar cascade (good enough for frontal webcam-style video); if no face is
found it falls back to a centred crop.
"""
from __future__ import annotations

import argparse
import base64
import csv
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "inference"))

from zerospoof.engine import LiveSession  # noqa: E402
from zerospoof.fusion import MODULES  # noqa: E402


def read_video(path: str, max_seconds: float):
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    box = None
    frames, patches = [], []
    i = 0
    while True:
        ok, bgr = cap.read()
        if not ok or i / fps > max_seconds:
            break
        h, w = bgr.shape[:2]
        if i % 10 == 0:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5, minSize=(max(40, w // 8), max(40, h // 8)))
            if len(faces):
                box = max(faces, key=lambda f: f[2] * f[3])
            elif box is None:
                box = (w // 4, h // 6, w // 2, h * 2 // 3)  # fallback: centred crop
        x, y, bw, bh = [int(v) for v in box]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        def roi_mean(x0, x1, y0, y1):
            r = rgb[y + int(y0 * bh): y + int(y1 * bh), x + int(x0 * bw): x + int(x1 * bw)]
            return r.reshape(-1, 3).mean(axis=0).tolist() if r.size else [0, 0, 0]

        rois = [roi_mean(0.30, 0.70, 0.08, 0.25), roi_mean(0.12, 0.38, 0.50, 0.75), roi_mean(0.62, 0.88, 0.50, 0.75)]
        t_ms = i * 1000.0 / fps
        frames.append({"t": t_ms, "rois": rois, "yaw": 0.0, "blink": 0.0})
        if i % max(1, int(round(fps / 5))) == 0:
            crop = cv2.cvtColor(bgr[y : y + bh, x : x + bw], cv2.COLOR_BGR2GRAY)
            if crop.size:
                patches.append({"t": t_ms, "data": base64.b64encode(cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA).tobytes()).decode()})
        i += 1
    cap.release()
    return frames, patches, fps


def read_audio(path: str):
    from scipy.io import wavfile

    sr, x = wavfile.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float32)
    if x.dtype.kind == "i" or np.abs(x).max() > 1.5:
        x = x / (np.abs(x).max() + 1e-9)
    return sr, x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--audio")
    ap.add_argument("--max-seconds", type=float, default=30.0)
    ap.add_argument("--label", choices=["bonafide", "attack"])
    ap.add_argument("--attack-type", default="unknown")
    ap.add_argument("--scores-csv", help="append label,attack_type,probability here (for evaluate.py --scores)")
    ap.add_argument("--modules-csv", help="append per-module scores here (for train_fusion.py --csv)")
    a = ap.parse_args()

    frames, patches, fps = read_video(a.video, a.max_seconds)
    live = LiveSession("offline", "offline", None, offline=True)
    for k in range(0, len(frames), 100):
        live.add_video({"frames": frames[k : k + 100], "patches": []})
    live.add_video({"frames": [], "patches": patches[:20]})
    for k in range(20, len(patches), 20):
        live.add_video({"frames": [], "patches": patches[k : k + 20]})
    if a.audio:
        sr, x = read_audio(a.audio)
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
        step = sr  # 1 s chunks
        for k in range(0, len(pcm), step):
            live.add_audio({"sr": sr, "pcm": base64.b64encode(pcm[k : k + step].tobytes()).decode()})

    res = live.analyze(final=True)
    print(f"video: {len(frames)} frames @ {fps:.1f} fps ({res['elapsed_s']} s)")
    print(f"verdict: {res['verdict'].upper()}   liveness probability: {res['probability']:.2f}")
    for n, m in res["modules"].items():
        state = f"score {m['score']:.2f} (conf {m['confidence']:.2f})" if m["available"] else f"unavailable: {m['details'].get('reason', '')}"
        print(f"  {n:12s} {state}")
    for f in res["flags"]:
        print(f"  [{f['severity']:6s}] {f['module']}: {f['message']}")

    if a.label and a.scores_csv:
        new = not os.path.exists(a.scores_csv)
        with open(a.scores_csv, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["label", "attack_type", "probability"])
            w.writerow([a.label, "" if a.label == "bonafide" else a.attack_type, res["probability"]])
    if a.label and a.modules_csv:
        new = not os.path.exists(a.modules_csv)
        with open(a.modules_csv, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["label"] + [c for m in MODULES for c in (m, f"{m}_conf")])
            row = [a.label]
            for m in MODULES:
                r = res["modules"].get(m)
                row += [r["score"], r["confidence"]] if r and r["available"] else ["", ""]
            w.writerow(row)


if __name__ == "__main__":
    main()
