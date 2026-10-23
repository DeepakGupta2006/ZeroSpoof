#!/usr/bin/env python3
"""Evaluate ZeroSpoof the way presentation-attack detection (ISO/IEC 30107-3) is reported.

Metrics (accuracy alone is a weak metric here, so it is NOT the headline):
  APCER  Attack Presentation Classification Error Rate  = attacks wrongly accepted (per attack type; report the worst)
  BPCER  Bona-fide Presentation Classification Error Rate = genuine users wrongly rejected
  ACER   (APCER + BPCER) / 2
  EER    point where APCER == BPCER when sweeping the threshold on the liveness probability
  Latency  time to produce one decision (target < ~500 ms)

Two modes
  1. synthetic (default): runs generated sessions through the REAL pipeline. This validates the
     plumbing and gives a latency number. It does NOT measure real-world accuracy.
  2. --scores file.csv: score real datasets. CSV columns: label,attack_type,probability
     (label = bonafide|attack). Produce it by running your dataset videos through
     tools/video_to_session.py (see evaluation/README.md).

Cross-dataset evaluation (train on one dataset, test on another) is done with --scores plus a
fusion trained on a different dataset; see evaluation/train_fusion.py.

Usage
  python evaluation/evaluate.py --n 30 --out evaluation/report.md
  python evaluation/evaluate.py --hard --n 30           # lower pulse amplitude / more noise
  python evaluation/evaluate.py --scores my_scores.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "inference"))
os.environ.setdefault("ZS_MODELS_DIR", str(Path(__file__).resolve().parent / "_no_models"))

from zerospoof import synth  # noqa: E402
from zerospoof.engine import LiveSession  # noqa: E402
from zerospoof.simclient import session_messages  # noqa: E402


def run_synthetic(kind: str, seed: int, hard: bool) -> tuple[float, dict, float, dict]:
    chall = ["turn_left", "turn_right", "blink_twice"][seed % 3]
    s = synth.generate(kind, seed=seed, challenge=chall, hard=hard)
    live = LiveSession(f"s{seed}", "k", {"type": chall, "nonce": "N"})
    for m in session_messages(s, "N"):
        t = m["type"]
        if t == "hello":
            live.add_hello(m)
        elif t == "video":
            live.add_video(m)
        elif t == "audio":
            live.add_audio(m)
        elif t == "challenge":
            live.set_challenge_window(m)
    t0 = time.perf_counter()
    res = live.analyze(final=True)
    ms = (time.perf_counter() - t0) * 1000
    feats = {n: m["score"] for n, m in res["modules"].items() if m["available"]}
    return res["probability"], feats, ms, res


def pad_metrics(bona: np.ndarray, attacks: dict[str, np.ndarray], thr: float) -> dict:
    """bona/attacks are liveness probabilities. Accept if p >= thr."""
    bpcer = float(np.mean(bona < thr)) if len(bona) else float("nan")
    per = {k: float(np.mean(v >= thr)) for k, v in attacks.items() if len(v)}
    apcer = max(per.values()) if per else float("nan")
    return {"thr": thr, "bpcer": bpcer, "apcer": apcer, "per_attack": per, "acer": (apcer + bpcer) / 2}


def eer(bona: np.ndarray, attacks: dict[str, np.ndarray]) -> tuple[float, float]:
    best = (1.0, 0.5)
    for thr in np.linspace(0, 1, 501):
        m = pad_metrics(bona, attacks, thr)
        gap = abs(m["apcer"] - m["bpcer"])
        if gap < best[0]:
            best = (gap, thr)
            eer_v = (m["apcer"] + m["bpcer"]) / 2
    return float(eer_v), float(best[1])


def report(bona: np.ndarray, attacks: dict[str, np.ndarray], lat: list[float] | None, header: str, thr: float = 0.70) -> str:
    m = pad_metrics(bona, attacks, thr)
    e, e_thr = eer(bona, attacks)
    lines = [f"# {header}", "", f"bona fide samples: {len(bona)}   attack samples: {sum(len(v) for v in attacks.values())}", "",
             f"Operating point (accept if p >= {thr:.2f}):",
             f"  BPCER = {m['bpcer']:.1%}", f"  APCER (worst attack type) = {m['apcer']:.1%}", f"  ACER  = {m['acer']:.1%}",
             f"  EER   = {e:.1%} at p = {e_thr:.2f}", "", "APCER per attack type:"]
    lines += [f"  {k:12s} {v:.1%}" for k, v in sorted(m["per_attack"].items())]
    if lat:
        a = np.array(lat)
        lines += ["", f"Decision latency (analysis of a full session, single CPU thread): median {np.median(a):.0f} ms, p95 {np.percentile(a, 95):.0f} ms",
                  "  (target < ~500 ms for an incremental update; a full 14 s session is the worst case)"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="sessions per kind (synthetic mode)")
    ap.add_argument("--hard", action="store_true", help="weaker pulse / more noise (compression-like)")
    ap.add_argument("--scores", help="CSV with label,attack_type,probability for real datasets")
    ap.add_argument("--thr", type=float, default=0.70)
    ap.add_argument("--out", help="write the report to this file")
    a = ap.parse_args()

    if a.scores:
        bona, attacks = [], defaultdict(list)
        with open(a.scores) as f:
            for row in csv.DictReader(f):
                p = float(row["probability"])
                (bona if row["label"] == "bonafide" else attacks[row["attack_type"]]).append(p)
        text = report(np.array(bona), {k: np.array(v) for k, v in attacks.items()}, None, f"ZeroSpoof PAD report: {a.scores}", a.thr)
    else:
        bona, attacks, lat = [], defaultdict(list), []
        feats_rows = []
        for kind in synth.KINDS:
            for seed in range(a.n):
                p, feats, ms, _ = run_synthetic(kind, 1000 + seed, a.hard)
                lat.append(ms)
                feats_rows.append((kind, feats))
                (bona if kind == "genuine" else attacks[kind]).append(p)
        text = report(np.array(bona), {k: np.array(v) for k, v in attacks.items()}, lat,
                      "ZeroSpoof PAD report (SYNTHETIC data: validates the pipeline, NOT real-world accuracy)" + (" [hard mode]" if a.hard else ""), a.thr)
        text += "\n\nNote: with the built-in heuristics, 'voice_clone' and 'deepfake' mostly land in REVIEW (counted as not-accepted), but a few are still accepted (non-zero APCER above).\nThese are the hardest attacks; real detection quality needs trained models and real datasets (evaluation/README.md)."
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
