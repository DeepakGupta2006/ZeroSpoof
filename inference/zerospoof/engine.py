"""LiveSession: buffers a stream, runs every detector, and fuses the result."""
from __future__ import annotations

import base64
import time
from math import gcd
from typing import Any

import numpy as np
from scipy import signal

from . import fusion
from .config import settings
from .detectors import ModuleResult, unavailable, audio, challenge, depth, fingerprint, moire, rppg

MAX_SECONDS = 40
MAX_FRAMES = int(MAX_SECONDS * 65)
MAX_PATCHES = 400
MAX_ROIS = 6
PATCH = moire.PATCH
AUDIO_SR = audio.SR


class ProtocolError(ValueError):
    pass


class LiveSession:
    def __init__(self, sid: str, key_id: str, chall: dict[str, Any] | None, offline: bool = False) -> None:
        self.offline = offline  # True for files: timestamps are synthetic, so skip the timing check
        self.sid, self.key_id, self.challenge = sid, key_id, chall
        self.t: list[float] = []
        self.rois: list[list[list[float]]] = []
        self.yaw: list[float] = []
        self.blink: list[float] = []
        self.patch_t: list[float] = []
        self.patches: list[np.ndarray] = []
        self.audio_chunks: list[np.ndarray] = []
        self.audio_samples = 0
        self.fingerprint: dict[str, Any] = {}
        self.face_tracker = True  # False when the browser could not load face-landmark tracking
        self.challenge_window: tuple[float, float] | None = None
        self.k_rois: int | None = None
        self.latencies: list[float] = []
        self.started = time.time()

    # ------------------------------------------------------------ ingestion
    def add_hello(self, msg: dict[str, Any]) -> None:
        fp = msg.get("fingerprint")
        if isinstance(fp, dict):
            self.fingerprint = fp
        self.face_tracker = bool(msg.get("faceTracker", True))

    def add_video(self, msg: dict[str, Any]) -> None:
        for fr in msg.get("frames", [])[:200]:
            if len(self.t) >= MAX_FRAMES:
                raise ProtocolError("too many frames")
            try:
                t = float(fr["t"])
                rois = np.asarray(fr["rois"], dtype=np.float64)
            except (KeyError, TypeError, ValueError):
                raise ProtocolError("bad frame")
            if rois.ndim != 2 or rois.shape[1] != 3 or not (1 <= rois.shape[0] <= MAX_ROIS) or not np.isfinite(rois).all():
                raise ProtocolError("bad rois")
            if self.k_rois is None:
                self.k_rois = rois.shape[0]
            if rois.shape[0] != self.k_rois or (self.t and t <= self.t[-1]):
                continue  # drop inconsistent / out-of-order frames
            self.t.append(t)
            self.rois.append(np.clip(rois, 0, 255).tolist())
            self.yaw.append(float(fr.get("yaw", 0.0)))
            self.blink.append(float(fr.get("blink", 0.0)))
        for p in msg.get("patches", [])[:20]:
            if len(self.patches) >= MAX_PATCHES:
                break
            try:
                raw = base64.b64decode(p["data"], validate=True)
                if len(raw) != PATCH * PATCH:
                    raise ValueError
                self.patches.append(np.frombuffer(raw, dtype=np.uint8).reshape(PATCH, PATCH).copy())
                self.patch_t.append(float(p.get("t", 0.0)))
            except Exception:
                raise ProtocolError("bad patch")

    def add_audio(self, msg: dict[str, Any]) -> None:
        sr = int(msg.get("sr", AUDIO_SR))
        if not (8000 <= sr <= 48000):
            raise ProtocolError("bad sample rate")
        try:
            pcm = np.frombuffer(base64.b64decode(msg["pcm"], validate=True), dtype="<i2").astype(np.float32) / 32768.0
        except Exception:
            raise ProtocolError("bad audio")
        if sr != AUDIO_SR:
            g = gcd(sr, AUDIO_SR)
            pcm = signal.resample_poly(pcm, AUDIO_SR // g, sr // g).astype(np.float32)
        if self.audio_samples + len(pcm) > MAX_SECONDS * AUDIO_SR:
            raise ProtocolError("too much audio")
        self.audio_chunks.append(pcm)
        self.audio_samples += len(pcm)

    def set_challenge_window(self, msg: dict[str, Any]) -> None:
        if not self.challenge or msg.get("nonce") != self.challenge["nonce"]:
            raise ProtocolError("challenge nonce mismatch")
        self.challenge_window = (float(msg["start"]), float(msg["end"]))

    # ------------------------------------------------------------ analysis
    @property
    def elapsed(self) -> float:
        return (self.t[-1] - self.t[0]) / 1000.0 if len(self.t) > 1 else 0.0

    def analyze(self, final: bool = False) -> dict[str, Any]:
        t0 = time.perf_counter()
        t = np.asarray(self.t)
        rois = np.asarray(self.rois) if self.rois else np.zeros((0, 1, 3))
        yaw, blink = np.asarray(self.yaw), np.asarray(self.blink)
        mods: dict[str, ModuleResult] = {}

        if len(t) > 1:
            mods["rppg"] = rppg.analyze(t, rois, min_seconds=settings.min_seconds, yaw=yaw, skip_window=self.challenge_window)
            mods["stream"] = fingerprint.stream_checks(t, rois, self.patches, check_timing=not self.offline)
            if self.face_tracker:
                mods["challenge"] = challenge.evaluate(self.challenge, t, yaw, blink, self.challenge_window)
            else:  # no landmarks on this device: the challenge cannot be judged, so it is not held against the user
                mods["challenge"] = unavailable("challenge", "face tracking unavailable in this browser")
        mods["moire"] = moire.analyze(self.patches)
        mods["depth"] = depth.analyze(self.patches)
        mods["audio"] = audio.analyze(np.concatenate(self.audio_chunks) if self.audio_chunks else None)
        mods["fingerprint"] = fingerprint.client_checks(self.fingerprint)

        verdict = fusion.decide(mods, self.elapsed, final)
        if not self.face_tracker and verdict.verdict == "pass":
            # Without the challenge a PASS is not allowed: send it to a human instead.
            verdict.verdict = "review"
            verdict.reasons.append("challenge could not be performed (face tracking unavailable): manual review")
        latency = (time.perf_counter() - t0) * 1000.0
        self.latencies.append(latency)

        flags: list[dict[str, str]] = []
        for m in mods.values():
            flags.extend(m.flags)
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        flags.sort(key=lambda f: order.get(f["severity"], 9))

        return {
            "verdict": verdict.verdict,
            "probability": round(verdict.probability, 4),
            "reasons": verdict.reasons,
            "elapsed_s": round(self.elapsed, 1),
            "modules": {n: m.to_dict() for n, m in mods.items()},
            "flags": flags,
            "latency_ms": {"last": round(latency, 1), "p50": round(float(np.median(self.latencies)), 1)},
        }
