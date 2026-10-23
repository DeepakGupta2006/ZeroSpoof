"""Audio anti-spoofing (voice clone / TTS played on the call).

Three tiers, best available wins:

 1. AASIST / RawNet2 ONNX  -> inference/models/audio.onnx  (raw 16 kHz waveform in, [spoof, bonafide] logits out).
                              This is the production path. Train on ASVspoof 2019 LA / 2021 and export.
 2. Trained feature baseline -> inference/models/audio_lr.json  (made by tools/train_audio_baseline.py
                              from folders of bona-fide / spoof WAVs, e.g. ASVspoof, WaveFake, In-the-Wild).
 3. Heuristic cues          -> always available, LOW confidence on purpose. Looks for waveform
                              discontinuities (spliced TTS chunks) and unnaturally steady pitch.
                              Modern neural TTS will often pass these, so do not rely on tier 3 alone.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import signal

from ..config import settings
from . import ModuleResult, unavailable

log = logging.getLogger("zerospoof.audio")
SR = 16000
AASIST_LEN = 64600  # ~4.04 s, the AASIST/RawNet2 input length


# ---------------------------------------------------------------- features
def _frames(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    n = 1 + max(0, (len(x) - win) // hop)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def f0_track(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Autocorrelation F0 per 40 ms frame (vectorised). Returns periods in samples for voiced frames."""
    win, hop = int(0.04 * sr), int(0.01 * sr)
    fr = _frames(x, win, hop)
    lo, hi = int(sr / 400), int(sr / 70)
    voiced = fr.std(axis=1) >= 0.01
    if not voiced.any():
        return np.array([])
    fr = fr[voiced]
    fr = fr - fr.mean(axis=1, keepdims=True)
    spec = np.fft.rfft(fr, 2048, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :win]
    ac = ac / (ac[:, :1] + 1e-12)
    seg = ac[:, lo:hi]
    k = np.argmax(seg, axis=1)
    ok = seg[np.arange(len(k)), k] > 0.5
    return (lo + k[ok]).astype(float)


def feature_vector(x: np.ndarray, sr: int = SR) -> dict[str, float]:
    """Compact, interpretable features used by both the heuristic and the trained baseline."""
    x = x.astype(np.float64)
    x = x / (np.max(np.abs(x)) + 1e-9)
    # Discontinuity ("click") detector: broadband steps show up as impulses in the >5 kHz band.
    sos = signal.butter(4, 5000, btype="highpass", fs=sr, output="sos")
    hp = np.abs(signal.sosfilt(sos, x))
    thr = 15.0 * (np.median(hp) + 1e-9)
    idx = np.flatnonzero(hp > thr)
    events = 0 if len(idx) == 0 else 1 + int(np.sum(np.diff(idx) > int(0.002 * sr)))
    clicks_per_s = float(events / (len(x) / sr))

    periods = f0_track(x, sr)
    if len(periods) > 10:
        jitter = float(np.mean(np.abs(np.diff(periods)) / periods[:-1]))
    else:
        jitter = float("nan")

    f, t, z = signal.stft(x, fs=sr, nperseg=512, noverlap=352)
    mag = np.abs(z) + 1e-9
    flat = np.exp(np.mean(np.log(mag), axis=0)) / np.mean(mag, axis=0)
    flux = np.sqrt(np.sum(np.diff(mag, axis=1) ** 2, axis=0))
    hf = float(mag[f > 4000].sum() / mag.sum())
    centroid = float((f[:, None] * mag).sum(0).mean() / mag.sum(0).mean())
    return {
        "clicks_per_s": clicks_per_s,
        "jitter": jitter,
        "flatness_mean": float(flat.mean()),
        "flatness_std": float(flat.std()),
        "flux_cv": float(flux.std() / (flux.mean() + 1e-9)),
        "hf_ratio": hf,
        "centroid_hz": centroid,
        "rms": float(np.sqrt(np.mean(np.asarray(x) ** 2))),
    }


FEATURE_ORDER = ["clicks_per_s", "jitter", "flatness_mean", "flatness_std", "flux_cv", "hf_ratio", "centroid_hz"]


def _vec(feats: dict[str, float], impute: dict[str, float]) -> np.ndarray:
    return np.array([impute.get(k, 0.0) if np.isnan(feats[k]) else feats[k] for k in FEATURE_ORDER])


# ---------------------------------------------------------------- tiers
@lru_cache(maxsize=1)
def _onnx():
    p = Path(settings.models_dir) / "audio.onnx"
    if not p.exists():
        return None
    try:
        import onnxruntime as ort

        return ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    except Exception as e:  # pragma: no cover
        log.warning("audio ONNX model could not be loaded: %s", e)
        return None


@lru_cache(maxsize=1)
def _baseline():
    p = Path(settings.models_dir) / "audio_lr.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:  # pragma: no cover
        log.warning("audio baseline could not be loaded: %s", e)
        return None


def _sigmoid(v: float) -> float:
    return float(1.0 / (1.0 + np.exp(-v)))


def analyze(audio: np.ndarray | None, sr: int = SR, min_seconds: float = 2.0) -> ModuleResult:
    name = "audio"
    if audio is None or len(audio) < sr * min_seconds:
        return unavailable(name, "not enough audio yet (or microphone not shared)")
    x = audio.astype(np.float32)[-6 * sr :]  # the most recent 6 s is plenty (AASIST uses ~4 s)
    if float(np.sqrt(np.mean(x**2))) < 0.003:
        return unavailable(name, "audio is silent")

    sess = _onnx()
    if sess is not None:
        seg = x[-AASIST_LEN:]
        if len(seg) < AASIST_LEN:
            seg = np.tile(seg, int(np.ceil(AASIST_LEN / len(seg))))[:AASIST_LEN]
        out = np.asarray(sess.run(None, {sess.get_inputs()[0].name: seg[None, :].astype(np.float32)})[0]).reshape(-1)
        e = np.exp(out - out.max())
        p_bona = float(e[-1] / e.sum())  # convention: last logit = bona fide
        res = ModuleResult(name, score=p_bona, confidence=0.9, details={"tier": "onnx", "p_bonafide": round(p_bona, 4)})
        if p_bona < 0.3:
            res.flag("synthetic_voice", "high", "Audio model rates the voice as synthetic or cloned.")
        return res

    feats = feature_vector(x, sr)
    base = _baseline()
    if base is not None:
        v = (_vec(feats, base["impute"]) - np.array(base["mean"])) / (np.array(base["std"]) + 1e-9)
        p_bona = _sigmoid(float(v @ np.array(base["w"]) + base["b"]))
        res = ModuleResult(name, score=p_bona, confidence=0.6, details={"tier": "baseline", "p_bonafide": round(p_bona, 4)})
        if p_bona < 0.3:
            res.flag("synthetic_voice", "medium", "Audio features resemble synthetic speech.")
        return res

    # Tier 3: heuristic cues (low confidence by design).
    spoof = 0.0
    cues = []
    if feats["clicks_per_s"] > 1.5:
        spoof += 0.45
        cues.append("waveform discontinuities")
    j = feats["jitter"]
    if not np.isnan(j) and j < 0.0012:
        spoof += 0.4
        cues.append("unnaturally steady pitch")
    score = float(np.clip(1.0 - spoof, 0.0, 1.0))
    res = ModuleResult(
        name,
        score=score,
        confidence=0.3,
        details={"tier": "heuristic", "clicks_per_s": round(feats["clicks_per_s"], 2),
                 "pitch_jitter": None if np.isnan(j) else round(j, 4), "cues": cues},
    )
    if spoof >= 0.8:
        res.flag("synthetic_voice", "low", "Audio shows several synthetic-speech cues (" + ", ".join(cues) + ").")
    return res
