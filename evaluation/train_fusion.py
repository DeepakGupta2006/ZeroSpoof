#!/usr/bin/env python3
"""Fit the fusion logistic regression and write inference/models/fusion.json.

Input: a CSV with one row per labelled session and the per-module scores/confidences, e.g. produced
by tools/video_to_session.py --modules-csv:

    label,rppg,rppg_conf,moire,moire_conf,depth,depth_conf,audio,audio_conf,fingerprint,fingerprint_conf,stream,stream_conf,challenge,challenge_conf
    bonafide,0.91,0.9,0.95,1,...

Missing modules: leave score and conf empty (treated as unavailable, contributes 0).

Without --csv it trains on SYNTHETIC sessions. That only demonstrates the mechanics: never ship weights
learned from synthetic data. Train on one real dataset and test on another (cross-dataset) before trusting it.

    python evaluation/train_fusion.py --csv modules.csv
    python evaluation/train_fusion.py --synthetic 40 --out /tmp/fusion.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "inference"))
os.environ.setdefault("ZS_MODELS_DIR", str(Path(__file__).resolve().parent / "_no_models"))

from zerospoof.fusion import MODULES  # noqa: E402


def row_features(scores: dict[str, tuple[float, float]]) -> np.ndarray:
    xs = [(s - 0.5) * c if c > 0 else 0.0 for s, c in (scores.get(m, (0.5, 0.0)) for m in MODULES)]
    xs.append(min(xs + [0.0]))
    return np.array(xs)


def fit_logreg(x: np.ndarray, y: np.ndarray, l2: float = 0.05, iters: int = 4000, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Plain L2-regularised logistic regression by gradient descent (no sklearn needed)."""
    w = np.zeros(x.shape[1])
    b = 0.0
    for _ in range(iters):
        z = x @ w + b
        p = 1 / (1 + np.exp(-z))
        g = p - y
        w -= lr * (x.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return w, float(b)


def load_csv(path: str):
    xs, ys = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            sc = {}
            for m in MODULES:
                s, c = row.get(m, ""), row.get(f"{m}_conf", "")
                if s != "" and c != "":
                    sc[m] = (float(s), float(c))
            xs.append(row_features(sc))
            ys.append(1.0 if row["label"] == "bonafide" else 0.0)
    return np.array(xs), np.array(ys)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--synthetic", type=int, default=0, help="sessions per kind")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "inference" / "models" / "fusion.json"))
    a = ap.parse_args()

    if a.csv:
        x, y = load_csv(a.csv)
    elif a.synthetic:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from evaluate import run_synthetic
        from zerospoof import synth

        xs, ys = [], []
        for kind in synth.KINDS:
            for seed in range(a.synthetic):
                _, feats, _, res = run_synthetic(kind, 5000 + seed, hard=bool(seed % 2))
                sc = {n: (m["score"], m["confidence"]) for n, m in res["modules"].items() if m["available"]}
                xs.append(row_features(sc))
                ys.append(1.0 if kind in ("genuine", "voice_clone") else 0.0)
        x, y = np.array(xs), np.array(ys)
    else:
        ap.error("give --csv or --synthetic N")

    w, b = fit_logreg(x, y)
    acc = float(np.mean(((x @ w + b) > 0) == (y > 0.5)))
    out = {"w": {m: float(w[i]) for i, m in enumerate(MODULES)}, "w_min": float(w[-1]), "bias": b, "scale": 1.0,
           "trained_on": "csv" if a.csv else "SYNTHETIC (demo only)", "n": int(len(y)), "train_accuracy": round(acc, 4)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nwritten to {a.out}")
    if not a.csv:
        print("WARNING: weights were learned from synthetic data. Delete the file before shipping.")


if __name__ == "__main__":
    main()
