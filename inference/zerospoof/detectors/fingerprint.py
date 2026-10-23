"""Device fingerprint + stream-integrity checks (virtual camera injection, looped video).

Client-side signals (sent once in the `hello` message):
  - camera/microphone device labels (OBS Virtual Camera, ManyCam, ...)
  - whether navigator.mediaDevices.getUserMedia & friends are still native code
  - webdriver / headless flags, hardware, screen, timezone

Server-side signals (computed from the stream itself):
  - frame-interval regularity (real cameras jitter; injected video is often too regular)
  - frozen / duplicated crops
  - loop detection (a pre-recorded clip repeated has a near-perfect autocorrelation peak)

Honest limitation: a determined attacker who controls the browser can forge client-side fields.
Treat them as strong *hints* that raise risk, and rely on the signal-level checks for the rest.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np

from . import ModuleResult

HIGH_RISK = re.compile(
    r"obs[\s\-_]*(virtual|studio)|virtual|manycam|xsplit|snap\s*camera|splitcam|v4l2loopback|dummy|"
    r"vcam|screen[\s\-_]*capture|unity\s*video|fake|e2esoft|youcam|loopback",
    re.I,
)
MEDIUM_RISK = re.compile(r"droidcam|iriun|epoccam|camo|ndi|logi\s*capture|streamlabs|mmhmm|nvidia\s*broadcast", re.I)
HEADLESS = re.compile(r"headless|phantomjs|puppeteer|playwright", re.I)


def summarize(fp: dict[str, Any]) -> dict[str, Any]:
    """Small, privacy-minimising summary kept for the dashboard (no raw ids)."""
    track = fp.get("activeTrack") or {}
    settings_ = track.get("settings") or {}
    cams = [c.get("label", "") for c in (fp.get("videoInputs") or [])]
    raw = "|".join(
        str(x) for x in [fp.get("userAgent"), fp.get("platform"), fp.get("webglRenderer"), fp.get("canvasHash"),
                         track.get("label"), (fp.get("screen") or {}).get("w"), (fp.get("screen") or {}).get("h")]
    )
    return {
        "device_id": hashlib.sha256(raw.encode()).hexdigest()[:16],
        "camera": track.get("label") or "(no label)",
        "resolution": f"{settings_.get('width', '?')}x{settings_.get('height', '?')}",
        "frame_rate": settings_.get("frameRate"),
        "platform": fp.get("platform"),
        "user_agent": (fp.get("userAgent") or "")[:140],
        "timezone": fp.get("timezone"),
        "cameras_listed": len(cams),
    }


def client_checks(fp: dict[str, Any]) -> ModuleResult:
    res = ModuleResult("fingerprint", score=1.0, confidence=0.0)
    if not fp:
        res.details = {"reason": "no fingerprint received"}
        return res
    res.confidence = 0.7
    penalty = 0.0
    labels = [(fp.get("activeTrack") or {}).get("label", "")] + [c.get("label", "") for c in fp.get("videoInputs") or []]
    active_label = labels[0]

    if active_label and HIGH_RISK.search(active_label):
        penalty += 0.9
        res.flag("virtual_camera", "high", f"Active camera looks like a virtual camera: '{active_label}'.")
    elif active_label and MEDIUM_RISK.search(active_label):
        penalty += 0.35
        res.flag("virtual_camera_possible", "medium", f"Active camera uses a virtual-camera driver: '{active_label}'.")
    elif not active_label:
        penalty += 0.25
        res.flag("camera_label_missing", "low", "Camera label is empty even though permission was granted.")
    elif any(HIGH_RISK.search(l or "") for l in labels[1:]):
        penalty += 0.1
        res.flag("virtual_camera_installed", "info", "A virtual camera driver is installed on this device (not in use).")

    native = fp.get("nativeApis") or {}
    tampered = [k for k, v in native.items() if v is False]
    if tampered:
        penalty += 0.6
        res.flag("api_tampering", "high", "Browser media APIs have been overridden: " + ", ".join(tampered) + ".")

    ua = fp.get("userAgent") or ""
    if fp.get("webdriver") or HEADLESS.search(ua):
        penalty += 0.4
        res.flag("automation", "medium", "Browser looks automated (webdriver/headless).")

    settings_ = (fp.get("activeTrack") or {}).get("settings") or {}
    if not settings_.get("deviceId") and not settings_.get("width"):
        penalty += 0.1

    res.score = float(np.clip(1.0 - penalty, 0.0, 1.0))
    res.details = {"penalty": round(penalty, 2), "camera": active_label or "(none)"}
    return res


def stream_checks(t_ms: np.ndarray, rois: np.ndarray, patch_data: list[np.ndarray], check_timing: bool = True) -> ModuleResult:
    """Signal-level integrity checks that do not trust the client's self-description."""
    res = ModuleResult("stream", score=1.0, confidence=0.0)
    if len(t_ms) < 60:
        res.details = {"reason": "not enough frames yet"}
        return res
    res.confidence = 0.7
    penalty = 0.0
    dt = np.diff(t_ms)
    dt = dt[(dt > 0) & (dt < 500)]
    cv = float(dt.std() / dt.mean()) if len(dt) > 10 else 1.0
    if check_timing and cv < 0.012:  # (offline files have synthetic, perfectly regular timestamps)
        penalty += 0.4
        res.flag("too_regular_timing", "medium", "Frame timing is unnaturally regular (typical of injected/virtual video).")

    dup = 0.0
    if len(patch_data) >= 6:
        hashes = [hashlib.md5(p.tobytes()).hexdigest() for p in patch_data]
        dup = 1.0 - len(set(hashes)) / len(hashes)
        if dup > 0.3:
            penalty += 0.5
            res.flag("frozen_frames", "high", "Many face crops are bit-identical (frozen or looped video).")

    loop_peak = 0.0
    lag_s = None
    if len(t_ms) >= 200:
        x = rois[:, :, :].mean(axis=1).sum(axis=1)  # luminance-like per-frame trace
        dur = (t_ms[-1] - t_ms[0]) / 1000.0
        fs = len(t_ms) / max(dur, 1e-3)
        # A looped clip repeats *everything*, including per-frame sensor noise. Real video never does,
        # and a genuine heartbeat (quasi-periodic) must not be mistaken for a loop, so we correlate the
        # high-frequency residual (>~3 Hz), not the pulse band.
        w = 5
        ma = np.convolve(x, np.ones(w) / w, mode="valid")
        x = x[w // 2 : w // 2 + len(ma)] - ma
        if x.std() < 1e-6:
            x = np.random.default_rng(0).standard_normal(len(x))  # perfectly static: nothing to compare
        x = (x - x.mean()) / (x.std() + 1e-9)
        n = len(x)
        ac = np.correlate(x, x, mode="full")[n - 1 :] / (n - np.arange(n))  # normalise by overlap length
        lo, hi = int(2.0 * fs), min(int(10.0 * fs), n // 2)
        if hi > lo:
            k = int(np.argmax(ac[lo:hi]))
            loop_peak = float(ac[lo + k])
            lag_s = (lo + k) / fs
            if loop_peak > 0.9:
                penalty += 0.7
                res.flag("looped_video", "high", f"Video repeats itself every ~{lag_s:.1f}s (pre-recorded loop).")

    res.score = float(np.clip(1.0 - penalty, 0.0, 1.0))
    res.details = {
        "frame_interval_cv": round(cv, 4),
        "duplicate_crops": round(dup, 3),
        "loop_autocorr": round(loop_peak, 3),
        "loop_lag_s": None if lag_s is None else round(lag_s, 2),
    }
    return res
