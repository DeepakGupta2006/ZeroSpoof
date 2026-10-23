"""Moire / screen-glare / print-halftone detector (replay and photo attacks).

When a camera films a screen or a printed photo, the pixel grid of the display (or the halftone dots
of the print) beats against the camera sensor grid. In the 2-D Fourier spectrum this shows up as
a few *narrow, strong peaks* far above the smooth local background. Natural skin texture has a
smooth, noise-like spectrum with no such isolated peaks.

We also look at specular glare (large saturated blobs typical of a phone/monitor in front of the lens).

Input: grayscale face crops (uint8, 128x128) sampled a few times per second.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import ModuleResult, unavailable

PATCH = 128
_WIN = np.outer(np.hanning(PATCH), np.hanning(PATCH)).astype(np.float32)
_YY, _XX = np.indices((PATCH, PATCH))
_R = np.hypot(_YY - PATCH // 2, _XX - PATCH // 2)
_MASK = (_R >= 10) & (_R <= 60)
# DC axes get ringing from the window / edges; ignore a thin cross through the centre.
_MASK &= ~((np.abs(_YY - PATCH // 2) <= 1) | (np.abs(_XX - PATCH // 2) <= 1))


def peak_residual(patch: np.ndarray) -> tuple[float, float, tuple[int, int]]:
    """Return (max log-spectrum residual, count of strong bins, location of the peak)."""
    x = patch.astype(np.float32)
    x = x - ndimage.gaussian_filter(x, 5)  # remove shading / low-frequency content
    spec = np.fft.fftshift(np.fft.fft2(x * _WIN))
    mag = np.log(np.abs(spec) + 1e-3)
    bg = ndimage.uniform_filter(mag, size=11)  # local mean of log-magnitude (fast; a lone peak barely moves it)
    res = (mag - bg) * _MASK
    idx = np.unravel_index(int(np.argmax(res)), res.shape)
    return float(res[idx]), float((res > 2.3).sum()), (int(idx[0]), int(idx[1]))


def glare_fraction(patch: np.ndarray) -> float:
    sat = patch >= 250
    if not sat.any():
        return 0.0
    labels, n = ndimage.label(sat)
    if n == 0:
        return 0.0
    sizes = ndimage.sum(sat, labels, range(1, n + 1))
    return float(sizes.max() / patch.size)


def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


def analyze(patches: list[np.ndarray], min_patches: int = 4) -> ModuleResult:
    name = "moire"
    if len(patches) < min_patches:
        return unavailable(name, "not enough face crops yet")
    sample = patches[-8:]
    peaks, counts, glares, locs = [], [], [], []
    for p in sample:
        pk, cnt, loc = peak_residual(p)
        peaks.append(pk)
        counts.append(cnt)
        glares.append(glare_fraction(p))
        locs.append(loc)

    med_peak = float(np.median(peaks))
    med_count = float(np.median(counts))
    max_glare = float(np.max(glares))

    # A moire peak persists at (about) the same frequency over time; random noise peaks wander.
    locs_arr = np.array(locs, dtype=float)
    spread = float(np.median(np.linalg.norm(locs_arr - np.median(locs_arr, axis=0), axis=1)))
    persistent = spread < 4.0

    spoof = _sigmoid((med_peak - 2.9) / 0.35)
    if persistent and med_peak > 2.6:
        spoof = max(spoof, 0.8)
    glare_spoof = _sigmoid((max_glare - 0.04) / 0.015)
    spoof = max(spoof, 0.85 * glare_spoof)

    score = float(np.clip(1.0 - spoof, 0.0, 1.0))
    res = ModuleResult(
        name=name,
        score=score,
        confidence=float(np.clip(len(sample) / 8.0, 0.4, 1.0)),
        details={
            "peak_residual": round(med_peak, 2),
            "strong_bins": round(med_count, 1),
            "peak_persistence_px": round(spread, 2),
            "max_glare_fraction": round(max_glare, 4),
        },
    )
    if spoof > 0.6 and med_peak > 2.6:
        res.flag("moire_pattern", "high", "Interference pattern typical of a camera filming a screen or printed photo.")
    if glare_spoof > 0.6:
        res.flag("screen_glare", "medium", "Large specular glare, typical of a screen or glossy photo in front of the camera.")
    return res
