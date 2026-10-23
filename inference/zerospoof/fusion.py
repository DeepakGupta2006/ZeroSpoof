"""Score fusion: per-module scores -> one liveness probability and a verdict.

Design goals (transparent scoring is a product feature):
  * A small logistic model combines per-module evidence. Weights live in models/fusion.json
    (written by evaluation/train_fusion.py), with sane hand-set defaults.
  * Modules with confidence 0 are ignored, not guessed.
  * Explicit, explainable rules sit on top: strong evidence of an attack from any trusted module
    caps the probability, and a PASS needs both a screen/print check and a liveness cue.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .config import settings
from .detectors import ModuleResult, unavailable

MODULES = ["rppg", "moire", "depth", "audio", "fingerprint", "stream", "challenge"]

DEFAULT_WEIGHTS = {
    "w": {"rppg": 2.2, "moire": 2.4, "depth": 2.0, "audio": 1.4, "fingerprint": 1.8, "stream": 1.8, "challenge": 2.4},
    "w_min": 3.5,
    "bias": 0.4,
    "scale": 1.0,
}


@lru_cache(maxsize=1)
def load_weights() -> dict[str, Any]:
    p = Path(settings.models_dir) / "fusion.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return DEFAULT_WEIGHTS


def features(mods: dict[str, ModuleResult]) -> np.ndarray:
    """Feature vector: centred, confidence-weighted score per module + the worst of them."""
    xs = []
    for n in MODULES:
        r = mods.get(n)
        xs.append(0.0 if r is None or not r.available else (r.score - 0.5) * r.confidence)
    xs.append(min(xs + [0.0]))  # worst evidence (<= 0)
    return np.array(xs)


def probability(mods: dict[str, ModuleResult]) -> float:
    w = load_weights()
    wv = np.array([w["w"][n] for n in MODULES] + [w["w_min"]])
    z = w["bias"] + w["scale"] * float(features(mods) @ wv)
    return float(1.0 / (1.0 + np.exp(-z)))


@dataclass
class Verdict:
    verdict: str  # pending | pass | review | fail
    probability: float
    reasons: list[str]


def decide(mods: dict[str, ModuleResult], elapsed_s: float, final: bool) -> Verdict:
    reasons: list[str] = []
    if settings.demo_relax:
        r = mods.get("rppg")
        if r is not None and r.available and r.score < 0.25:
            mods = {**mods, "rppg": unavailable("rppg", "demo mode: inconclusive pulse ignored")}
            reasons.append("DEMO MODE: inconclusive pulse ignored")
    p = probability(mods)

    # --- hard rules: strong, trusted evidence of an attack caps the score
    for n, r in mods.items():
        if not r.available:
            continue
        if r.confidence >= 0.5 and r.score <= 0.12 and n in ("moire", "fingerprint", "stream", "depth", "challenge", "audio"):
            if n == "audio" and r.confidence < 0.6:
                continue  # weak heuristic audio never decides on its own
            p = min(p, 0.15)
            reasons.append(f"{n}: strong attack evidence")
        elif r.confidence >= 0.5 and r.score <= 0.3 and n != "audio":
            p = min(p, 0.55)
            reasons.append(f"{n}: suspicious")

    audio_r = mods.get("audio")
    if audio_r and audio_r.available and any(f["code"] == "synthetic_voice" for f in audio_r.flags):
        p = min(p, 0.6)  # synthetic-voice cue alone never fails a session, but it always forces a human review
        reasons.append("audio: possible synthetic voice")

    for n in ("rppg",):
        r = mods.get(n)
        if r and r.available and r.confidence >= 0.8 and r.score < 0.2:
            p = min(p, 0.55)
            reasons.append("rppg: no reliable pulse")

    # --- a PASS needs a print/screen check AND a liveness cue
    moire_ok = mods.get("moire") is not None and mods["moire"].available
    live_cue = any(mods.get(n) is not None and mods[n].available for n in ("rppg", "challenge", "depth"))
    if not (moire_ok and live_cue):
        p = min(p, 0.65)
        reasons.append("not enough independent evidence to pass")

    if elapsed_s < settings.min_seconds:
        if not final:
            return Verdict("pending", p, reasons)
        p = min(p, 0.65)
        reasons.append("session shorter than the minimum duration")

    if p >= settings.pass_threshold:
        v = "pass"
    elif p < settings.fail_threshold:
        v = "fail"
    else:
        v = "review"
    return Verdict(v, p, reasons)
