"""rPPG (remote photoplethysmography) liveness.

Real skin changes colour very slightly with every heartbeat. We receive the mean RGB of a few
face regions (forehead + both cheeks) per frame from the browser, then:

 1. resample to a uniform 30 Hz grid,
 2. extract a pulse signal per region with the classical POS method (Wang et al. 2017),
 3. band-pass 0.7-4 Hz and find the dominant frequency (heart rate),
 4. measure SNR (de Haan & Jeanne 2013 style), stability over time, and *coherence between
    regions* (a real pulse is in phase across the face; a face-swap blend or noise is not).

Caveat (honest): rPPG degrades on compressed video and in poor light. It is one signal among
several, which is why the module also reports a confidence that the fusion step respects.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

from . import ModuleResult, unavailable

FS = 30.0
BAND = (0.7, 4.0)  # Hz  (42-240 bpm)
HR_RANGE = (0.7, 3.0)  # Hz  (42-180 bpm) accepted as a plausible heart rate


def resample_uniform(t_ms: np.ndarray, rois: np.ndarray, fs: float = FS) -> np.ndarray:
    """rois: (N, K, 3) -> (M, K, 3) on a uniform grid."""
    t = (t_ms - t_ms[0]) / 1000.0
    m = int(t[-1] * fs)
    tu = np.arange(m) / fs
    n, k, _ = rois.shape
    out = np.empty((m, k, 3), dtype=np.float64)
    for ki in range(k):
        for c in range(3):
            out[:, ki, c] = np.interp(tu, t, rois[:, ki, c])
    return out


def pos(rgb: np.ndarray, fs: float = FS, win_s: float = 1.6) -> np.ndarray:
    """POS pulse extraction. rgb: (N, 3) -> (N,) pulse signal."""
    n = rgb.shape[0]
    win = int(win_s * fs)
    if n < win:
        return np.zeros(n)
    proj = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])
    h = np.zeros(n)
    for start in range(0, n - win + 1):
        c = rgb[start : start + win].T  # 3 x win
        mean = c.mean(axis=1, keepdims=True)
        mean[mean == 0] = 1.0
        s = proj @ (c / mean)
        sd1, sd2 = s[0].std(), s[1].std()
        alpha = sd1 / sd2 if sd2 > 1e-12 else 0.0
        seg = s[0] + alpha * s[1]
        h[start : start + win] += seg - seg.mean()
    return h


def bandpass(x: np.ndarray, fs: float = FS) -> np.ndarray:
    sos = signal.butter(3, BAND, btype="bandpass", fs=fs, output="sos")
    return signal.sosfiltfilt(sos, x)


def _spectrum(x: np.ndarray, fs: float = FS):
    nperseg = min(len(x), int(8 * fs))
    f, p = signal.welch(x, fs=fs, nperseg=nperseg, noverlap=int(nperseg * 0.75), nfft=4096)
    return f, p


def peak_and_snr(x: np.ndarray, fs: float = FS) -> tuple[float, float]:
    """Return (heart-rate Hz, SNR dB) of a band-passed signal."""
    f, p = _spectrum(x, fs)
    band = (f >= HR_RANGE[0]) & (f <= HR_RANGE[1])
    if not band.any() or p[band].sum() <= 0:
        return 0.0, -20.0
    fpk = f[band][np.argmax(p[band])]
    full = (f >= BAND[0]) & (f <= BAND[1])
    sig_mask = (np.abs(f - fpk) <= 0.12) | (np.abs(f - 2 * fpk) <= 0.12)
    sig_mask &= full
    s = p[sig_mask].sum()
    nse = p[full & ~sig_mask].sum()
    if nse <= 0:
        return float(fpk), 20.0
    return float(fpk), float(10 * np.log10(max(s, 1e-20) / nse))


def still_segment(t_ms: np.ndarray, yaw: np.ndarray | None, skip: tuple[float, float] | None,
                  max_yaw_speed: float = 25.0, pad_ms: float = 400.0) -> tuple[slice, str]:
    """Pick the longest stretch of frames where the head is (almost) still and no challenge is running.

    Head turns and the challenge window move the face regions across the image, which swamps the
    ~0.1% colour change of a pulse. Returns (slice, note). If nothing usable is found we return the
    whole recording, so movement can never be used to dodge the pulse check: it just scores low.
    """
    n = len(t_ms)
    ok = np.ones(n, dtype=bool)
    if skip is not None:
        ok &= ~((t_ms >= skip[0] - pad_ms) & (t_ms <= skip[1] + pad_ms))
    if yaw is not None and len(yaw) == n and n > 3:
        dt = np.maximum(np.gradient(t_ms) / 1000.0, 1e-3)
        speed = np.abs(np.gradient(yaw) / dt)
        speed = np.convolve(speed, np.ones(5) / 5, mode="same")
        moving = speed > max_yaw_speed
        k = max(1, int(pad_ms / max(1.0, float(np.median(np.diff(t_ms))))))
        moving = np.convolve(moving.astype(float), np.ones(2 * k + 1), mode="same") > 0
        ok &= ~moving
    best, cur_start = (0, 0), None
    for i in range(n + 1):
        if i < n and ok[i]:
            cur_start = i if cur_start is None else cur_start
        elif cur_start is not None:
            if i - cur_start > best[1] - best[0]:
                best = (cur_start, i)
            cur_start = None
    a, b = best
    if b - a < 2 or (t_ms[b - 1] - t_ms[a]) < 4000.0:
        return slice(0, n), "all frames (no long still segment)"
    return slice(a, b), "still segment"


def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


def analyze(t_ms: np.ndarray, rois: np.ndarray, min_seconds: float = 8.0,
            yaw: np.ndarray | None = None, skip_window: tuple[float, float] | None = None) -> ModuleResult:
    """t_ms: (N,), rois: (N, K, 3) mean RGB per region (0..255).

    yaw / skip_window (optional) let the analysis ignore the moving part of the session (see still_segment).
    """
    name = "rppg"
    segment_note = "all frames"
    if len(t_ms) > 10:
        sl, segment_note = still_segment(t_ms, yaw, skip_window)
        t_ms, rois = t_ms[sl], rois[sl]
    if len(t_ms) < int(min_seconds * 10):
        return unavailable(name, "not enough frames yet")
    duration = (t_ms[-1] - t_ms[0]) / 1000.0
    if duration < min_seconds:
        return unavailable(name, f"need {min_seconds:.0f}s of video, have {duration:.1f}s")
    if rois.ndim != 3 or rois.shape[2] != 3:
        return unavailable(name, "bad ROI shape")

    x = resample_uniform(t_ms, rois)
    k = x.shape[1]
    sigs = np.stack([bandpass(pos(x[:, i, :])) for i in range(k)])  # (K, M)
    comb = sigs.mean(axis=0)

    hr_hz, snr_db = peak_and_snr(comb)

    # Stability: heart rate estimated on 3 overlapping sub-windows should agree.
    m = comb.shape[0]
    w = int(m * 0.6)
    starts = [0, (m - w) // 2, m - w]
    hrs = [peak_and_snr(comb[s : s + w])[0] for s in starts]
    hr_std_bpm = float(np.std(hrs) * 60.0)
    stability = float(np.exp(-hr_std_bpm / 8.0))

    # Coherence between regions (band-passed POS signals of a real face move together).
    if k >= 2:
        cc = np.corrcoef(sigs)
        iu = np.triu_indices(k, 1)
        coherence = float(np.clip(cc[iu].mean(), 0.0, 1.0))
    else:
        coherence = 0.5

    # Lighting / signal-quality estimate -> confidence.
    luma = x.mean(axis=(1, 2))
    mean_luma = float(luma.mean())
    lighting_ok = 1.0 if 50 <= mean_luma <= 215 else 0.5
    dur_factor = float(np.clip(duration / 12.0, 0.4, 1.0))
    confidence = lighting_ok * dur_factor

    snr_term = _sigmoid((snr_db - 2.0) / 1.5)
    plausible = HR_RANGE[0] + 0.02 <= hr_hz <= HR_RANGE[1] - 0.02
    score = snr_term * (0.4 + 0.6 * stability) * (0.6 + 0.4 * coherence)
    if not plausible:
        score *= 0.5
    score = float(np.clip(score, 0.0, 1.0))

    res = ModuleResult(
        name=name,
        score=score,
        confidence=confidence,
        details={
            "bpm": round(hr_hz * 60.0, 1),
            "snr_db": round(snr_db, 2),
            "hr_std_bpm": round(hr_std_bpm, 2),
            "coherence": round(coherence, 3),
            "mean_luma": round(mean_luma, 1),
            "seconds": round(duration, 1),
            "segment": segment_note,
        },
    )
    if score < 0.25 and confidence >= 0.5:
        res.flag("no_pulse", "medium", "No consistent heartbeat signal found in the face region.")
    if k >= 2 and coherence < 0.15 and snr_db > 0:
        res.flag("incoherent_pulse", "medium", "Pulse signal differs between face regions (possible blended/swapped face).")
    if lighting_ok < 1.0:
        res.flag("poor_lighting", "info", "Lighting is too dark or too bright for reliable pulse estimation.")
    return res
