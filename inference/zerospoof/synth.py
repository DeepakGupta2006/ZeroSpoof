"""Synthetic session generator.

Used by the tests, the evaluation scripts and tools/simulate_client.py to exercise the whole
pipeline WITHOUT a camera or real datasets. IMPORTANT: this is a *plumbing* test. Numbers measured on
synthetic data prove the code works end to end; they say nothing about accuracy on real attacks.
For real numbers use evaluation/README.md (FaceForensics++, Replay-Attack, ASVspoof, ...).

Kinds: genuine | replay | photo | deepfake | virtual_cam | voice_clone
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

KINDS = ["genuine", "replay", "photo", "deepfake", "virtual_cam", "voice_clone"]
FS = 30.0
PATCH = 128


@dataclass
class SynthSession:
    kind: str
    duration: float
    frames: list[dict[str, Any]] = field(default_factory=list)  # {t, rois, yaw, blink}
    patches: list[dict[str, Any]] = field(default_factory=list)  # {t, data (128x128 uint8)}
    audio: np.ndarray | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    challenge_window: tuple[float, float] | None = None


# ------------------------------------------------------------------ video
def _pink(rng: np.random.Generator, shape: tuple[int, int]) -> np.ndarray:
    out = np.zeros(shape)
    for s, a in ((1.0, 1.0), (2.0, 1.4), (4.0, 2.0), (8.0, 2.4)):
        n = ndimage.gaussian_filter(rng.standard_normal(shape), s)
        out += a * n / (n.std() + 1e-9)
    return out / out.std()


def _face_patch(rng: np.random.Generator, base_noise: np.ndarray, yaw_deg: float, brightness: float) -> np.ndarray:
    yy, xx = np.mgrid[0:PATCH, 0:PATCH].astype(np.float64)
    cx = PATCH / 2 + yaw_deg * 0.6
    blob = np.exp(-(((xx - cx) / 48) ** 2 + ((yy - PATCH / 2) / 58) ** 2))
    img = brightness * (0.55 + 0.45 * blob)
    # eyes, nose, mouth shift with yaw (3-D-like motion)
    for ex, ey, s, a in ((-20, -12, 5, 28), (20, -12, 5, 28), (0, 28, 9, 22), (0, 4, 4, 10)):
        px = cx + ex * (1 + 0.004 * yaw_deg * np.sign(ex or 1)) + yaw_deg * 0.3
        img -= a * np.exp(-(((xx - px) / s) ** 2 + ((yy - PATCH / 2 - ey) / (s * 0.7)) ** 2))
    img += 5.0 * base_noise  # skin texture
    img += rng.normal(0, 1.3, img.shape)  # sensor noise
    return np.clip(img, 0, 255)


def _rois(rng: np.random.Generator, n: int, kind: str, tone: float, hard: bool) -> np.ndarray:
    t = np.arange(n) / FS
    bpm = rng.uniform(55, 100)
    f = bpm / 60.0
    base = rng.uniform([125, 92, 80], [185, 135, 118]) * tone
    amp = rng.uniform(0.7, 1.2) * (0.55 if hard else 1.0)
    if kind in ("photo",):
        amp = 0.0
    elif kind == "replay":
        amp *= 0.12  # video-of-a-video: pulse mostly gone after re-capture + compression
    elif kind == "deepfake":
        amp *= 0.45
    rel = np.array([0.0012, 0.0058, 0.0032]) * amp
    drift_f = rng.uniform(0.04, 0.25)
    drift = 0.012 * np.sin(2 * np.pi * drift_f * t + rng.uniform(0, 6.28))
    out = np.zeros((n, 3, 3))
    phase0 = rng.uniform(0, 6.28)
    for k in range(3):
        if kind == "deepfake":  # blended face: regions do not share a heartbeat
            fk = f * rng.uniform(0.7, 1.4)
            ph = rng.uniform(0, 6.28)
        else:
            fk, ph = f, phase0 + rng.normal(0, 0.12)
        bvp = np.sin(2 * np.pi * fk * t + ph) + 0.25 * np.sin(4 * np.pi * fk * t + 2 * ph)
        for c in range(3):
            noise = rng.normal(0, 0.0016 * (1.6 if hard else 1.0), n)
            out[:, k, c] = base[c] * (1 + drift + rel[c] * bvp + noise)
    return out


def _yaw_blink(rng: np.random.Generator, n: int, kind: str, challenge: str, win: tuple[float, float]):
    t = np.arange(n) / FS
    yaw = 1.2 * np.sin(2 * np.pi * rng.uniform(0.1, 0.3) * t + rng.uniform(0, 6.28)) + rng.normal(0, 0.15, n)
    blink = np.clip(rng.normal(0.05, 0.03, n), 0, 1)
    # natural spontaneous blinks
    for _ in range(int(len(t) / FS / 4)):
        i = int(rng.uniform(0, n - 6))
        blink[i : i + 4] = 0.9
    responds = kind in ("genuine", "deepfake", "voice_clone")  # live humans / live face-swap respond
    s, e = int(win[0] / 1000 * FS), int(win[1] / 1000 * FS)
    if responds:
        if challenge in ("turn_left", "turn_right"):
            sign = -1.0 if challenge == "turn_left" else 1.0
            ramp = np.sin(np.linspace(0, np.pi, e - s))
            yaw[s:e] += sign * rng.uniform(20, 32) * ramp
        else:
            for off in (0.8, 2.0):
                i = s + int(off * FS)
                blink[i : i + 4] = 0.92
    elif kind in ("replay", "virtual_cam"):
        # a pre-recorded clip has its own, unrelated motion
        yaw += 5.0 * np.sin(2 * np.pi * 0.2 * t + 1.0)
    return yaw, blink


_REAL_CAMS = ["FaceTime HD Camera", "Integrated Camera (04f2:b6cb)", "Logitech BRIO", "HD Webcam C920"]


def _fingerprint(rng: np.random.Generator, kind: str) -> dict[str, Any]:
    label = rng.choice(_REAL_CAMS)
    native = {"getUserMedia": True, "enumerateDevices": True, "getSettings": True}
    if kind == "virtual_cam":
        label = "OBS Virtual Camera"
        if rng.random() < 0.5:
            native["getUserMedia"] = False
    return {
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "platform": "Win32",
        "languages": ["en-US"],
        "hardwareConcurrency": 8,
        "screen": {"w": 1920, "h": 1080, "dpr": 1},
        "timezone": "Asia/Calcutta",
        "webdriver": False,
        "webglRenderer": "ANGLE (Intel, Mesa Intel UHD)",
        "canvasHash": f"{rng.integers(0, 2**31):08x}",
        "activeTrack": {"label": str(label), "settings": {"width": 1280, "height": 720, "frameRate": 30, "deviceId": "x", "facingMode": "user"}},
        "videoInputs": [{"label": str(label)}],
        "nativeApis": native,
    }


# ------------------------------------------------------------------ audio
def _voice(rng: np.random.Generator, seconds: float, kind: str, sr: int = 16000) -> np.ndarray:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    synthetic = kind == "voice_clone"
    f0_base = rng.uniform(100, 210)
    wander = np.cumsum(rng.normal(0, 0.6 if not synthetic else 0.12, n // 160 + 1))
    f0 = f0_base * (1 + 0.04 * np.interp(t, np.arange(len(wander)) / 100, wander) / (np.abs(wander).max() + 1e-9))
    if not synthetic:
        # period-to-period jitter ~1%
        jit = ndimage.gaussian_filter1d(rng.normal(0, 1.0, n), sr / 800)
        f0 = f0 * (1 + 0.012 * jit / (jit.std() + 1e-9))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    x = sum((1.0 / h) * np.sin(h * phase) for h in range(1, 18))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 3.5 * t + rng.uniform(0, 6)) ** 2
    x = x * env
    x += rng.normal(0, 0.02 if not synthetic else 0.004, n)
    x = x / (np.abs(x).max() + 1e-9) * 0.5
    if synthetic:
        for i in range(int(0.5 * sr), n, int(0.5 * sr)):  # spliced chunk boundaries -> clicks
            x[i:] += 0.12 * np.sign(rng.normal())
        x = np.clip(x, -0.9, 0.9)
    return x.astype(np.float32)


# ------------------------------------------------------------------ public
def generate(kind: str, duration: float = 14.0, seed: int | None = None, challenge: str = "turn_left",
             hard: bool = False, with_audio: bool = True) -> SynthSession:
    assert kind in KINDS, kind
    rng = np.random.default_rng(seed)
    n = int(duration * FS)
    tone = rng.uniform(0.55, 1.15)  # skin tone / exposure variation
    brightness = rng.uniform(130, 175) * min(tone, 1.0) + 25
    win = ((duration - 4.0) * 1000.0, (duration - 0.3) * 1000.0)

    if kind == "virtual_cam":  # short pre-recorded clip, looped
        loop_n = int(4.0 * FS)
        clip = _rois(rng, loop_n, "genuine", tone, hard)
        rois = np.tile(clip, (int(np.ceil(n / loop_n)), 1, 1))[:n]
        dts = np.full(n, 1000.0 / FS)  # perfectly regular timestamps
    else:
        rois = _rois(rng, n, kind, tone, hard)
        dts = rng.normal(1000.0 / FS, 1.8, n).clip(20, 60)
    t_ms = np.cumsum(dts)
    # keep the challenge window in the same clock as the timestamps
    win = (float(t_ms[int(win[0] / 1000 * FS)]), float(t_ms[min(n - 1, int(win[1] / 1000 * FS))]))
    yaw, blink = _yaw_blink(rng, n, kind, challenge, (win[0] - t_ms[0], win[1] - t_ms[0]))

    s = SynthSession(kind=kind, duration=duration)
    s.challenge_window = win
    base_noise = _pink(rng, (PATCH, PATCH))
    screen_f = rng.uniform(0.12, 0.34, 2) * rng.choice([-1, 1], 2)
    halftone_f = rng.uniform(0.22, 0.38)
    glare_on = rng.random() < 0.5
    prev = None
    for i in range(n):
        s.frames.append({"t": float(t_ms[i]), "rois": rois[i].tolist(), "yaw": float(yaw[i]), "blink": float(blink[i])})
        if i % 6 == 0:  # 5 crops / second
            if kind == "photo":
                img = _face_patch(rng, base_noise, 0.0, brightness) if prev is None else prev + rng.normal(0, 0.8, prev.shape)
                prev = img
                yy, xx = np.mgrid[0:PATCH, 0:PATCH]
                img = ndimage.gaussian_filter(img, 0.7) + 4.5 * np.cos(2 * np.pi * halftone_f * (xx * 0.8 + yy * 0.6))
            elif kind == "replay":
                img = _face_patch(rng, base_noise, float(yaw[i]), brightness)
                yy, xx = np.mgrid[0:PATCH, 0:PATCH]
                img = ndimage.gaussian_filter(img, 0.6) + rng.uniform(3.5, 6.5) * np.cos(2 * np.pi * (screen_f[0] * xx + screen_f[1] * yy))
                if glare_on:
                    img += 140 * np.exp(-(((xx - 95) / 16) ** 2 + ((yy - 30) / 12) ** 2))
            elif kind == "virtual_cam":
                j = (i % int(4.0 * FS))
                img = _face_patch(np.random.default_rng(1000 + j), base_noise, float(yaw[i]), brightness)  # frames repeat per loop
            else:
                img = _face_patch(rng, base_noise, float(yaw[i]), brightness)
            s.patches.append({"t": float(t_ms[i]), "data": np.clip(img, 0, 255).astype(np.uint8)})

    s.fingerprint = _fingerprint(rng, kind)
    s.audio = _voice(rng, duration, kind) if with_audio else None
    return s
