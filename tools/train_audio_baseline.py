#!/usr/bin/env python3
"""Train the tier-2 audio baseline (logistic regression on interpretable spectral features).

Folder layout (any WAV/FLAC-as-WAV set, e.g. ASVspoof 2019 LA, WaveFake, In-the-Wild):

    data/bonafide/*.wav
    data/spoof/*.wav

    python tools/train_audio_baseline.py data/ --out inference/models/audio_lr.json

This is a *baseline*: it is quick and explainable, but the production path is AASIST/RawNet2 as an
ONNX model (inference/models/audio.onnx, see docs/MODELS.md). Evaluate on a different corpus than you
train on (cross-dataset), because voice-spoof detectors often fail on unseen generators.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "inference"))

from scipy.io import wavfile  # noqa: E402
from scipy import signal  # noqa: E402

from zerospoof.detectors.audio import FEATURE_ORDER, SR, feature_vector  # noqa: E402


def load(path: Path) -> np.ndarray:
    sr, x = wavfile.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float32)
    x = x / (np.abs(x).max() + 1e-9)
    if sr != SR:
        from math import gcd

        g = gcd(sr, SR)
        x = signal.resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x[: 6 * SR]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "inference" / "models" / "audio_lr.json"))
    a = ap.parse_args()

    rows, ys = [], []
    for label, sub in ((1.0, "bonafide"), (0.0, "spoof")):
        files = sorted((Path(a.data) / sub).glob("*.wav"))
        if not files:
            sys.exit(f"no WAV files in {Path(a.data) / sub}")
        for f in files:
            feats = feature_vector(load(f))
            rows.append([feats[k] for k in FEATURE_ORDER])
            ys.append(label)
    x, y = np.array(rows, dtype=np.float64), np.array(ys)
    impute = {k: float(np.nanmedian(x[:, i])) for i, k in enumerate(FEATURE_ORDER)}
    for i, k in enumerate(FEATURE_ORDER):
        x[np.isnan(x[:, i]), i] = impute[k]
    mean, std = x.mean(axis=0), x.std(axis=0) + 1e-9
    z = (x - mean) / std
    w, b = np.zeros(z.shape[1]), 0.0
    for _ in range(3000):
        p = 1 / (1 + np.exp(-(z @ w + b)))
        g = p - y
        w -= 0.3 * (z.T @ g / len(y) + 0.02 * w)
        b -= 0.3 * g.mean()
    acc = float(np.mean(((z @ w + b) > 0) == (y > 0.5)))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"features": FEATURE_ORDER, "mean": mean.tolist(), "std": std.tolist(),
                                       "w": w.tolist(), "b": float(b), "impute": impute, "train_accuracy": acc}, indent=2))
    print(f"trained on {len(y)} clips, train accuracy {acc:.1%} -> {a.out}\nNow evaluate on a DIFFERENT dataset before trusting it.")


if __name__ == "__main__":
    main()
