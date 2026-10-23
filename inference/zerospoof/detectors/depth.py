"""Depth / planarity check (photo, mask-free screen attacks).

A real face is a 3-D surface (nose closer than cheeks, cheeks closer than ears); a photo or screen is a
plane. With a monocular depth network (MiDaS-small or Depth-Anything exported to ONNX) we estimate a
relative depth map of the face crop, fit a plane to it, and measure how much relief is left over.

The ONNX file is OPTIONAL. Without it this module reports "unavailable" and the fusion layer simply
ignores it (it never pretends to have a depth signal it doesn't have). To enable it:

    python tools/get_models.py          # tries to download MiDaS-small ONNX into inference/models/
    # or place any monocular depth ONNX at inference/models/depth.onnx  (NCHW float32 RGB input)

Limitation: the browser sends grayscale 128x128 crops, so the gray channel is replicated to RGB. That is
fine for a first version; send RGB crops if you want the best depth quality.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import settings
from . import ModuleResult, unavailable

log = logging.getLogger("zerospoof.depth")

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)


@lru_cache(maxsize=1)
def _session():
    path = Path(settings.models_dir) / "depth.onnx"
    if not path.exists():
        return None
    try:
        import onnxruntime as ort  # optional dependency

        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as e:  # pragma: no cover - depends on optional install
        log.warning("depth model could not be loaded: %s", e)
        return None


def relief_metrics(depth: np.ndarray) -> dict[str, float]:
    """Measure 3-D-ness of a depth map (any scale). Pure function, unit-tested without a model."""
    d = depth.astype(np.float64)
    h, w = d.shape
    d = (d - d.mean()) / (d.std() + 1e-9)
    yy, xx = np.mgrid[0:h, 0:w]
    a = np.stack([xx.ravel() / w, yy.ravel() / h, np.ones(h * w)], axis=1)
    coef, *_ = np.linalg.lstsq(a, d.ravel(), rcond=None)
    resid = d.ravel() - a @ coef
    planarity_resid = float(np.sqrt(np.mean(resid**2)))  # 0 for a perfect plane, ~1 for pure relief
    # centre-vs-border relief: a face bulges towards the camera in the middle
    cy, cx = slice(h // 4, 3 * h // 4), slice(w // 4, 3 * w // 4)
    centre = d[cy, cx].mean()
    border = np.concatenate([d[:h // 8].ravel(), d[-h // 8 :].ravel(), d[:, :w // 8].ravel(), d[:, -w // 8 :].ravel()]).mean()
    return {"plane_residual": planarity_resid, "centre_minus_border": float(abs(centre - border))}


def score_from_metrics(m: dict[str, float]) -> float:
    z = (m["plane_residual"] - 0.35) / 0.12
    return float(1.0 / (1.0 + np.exp(-z)))


def analyze(patches: list[np.ndarray], min_patches: int = 3) -> ModuleResult:
    name = "depth"
    sess = _session()
    if sess is None:
        return unavailable(name, "no depth model installed (optional; see docs)")
    if len(patches) < min_patches:
        return unavailable(name, "not enough face crops yet")
    inp = sess.get_inputs()[0]
    shape = inp.shape  # e.g. [1, 3, 256, 256]
    try:
        size = int(shape[2]) if isinstance(shape[2], int) else 256
    except Exception:
        size = 256
    from scipy import ndimage

    scores, mets = [], []
    for p in patches[-3:]:
        x = ndimage.zoom(p.astype(np.float32) / 255.0, size / p.shape[0], order=1)
        x = np.repeat(x[None, None, :, :], 3, axis=1)
        x = (x - _MEAN) / _STD
        out = np.asarray(sess.run(None, {inp.name: x.astype(np.float32)})[0]).squeeze()
        m = relief_metrics(out)
        mets.append(m)
        scores.append(score_from_metrics(m))
    score = float(np.mean(scores))
    res = ModuleResult(
        name=name,
        score=score,
        confidence=0.8,
        details={
            "plane_residual": round(float(np.mean([m["plane_residual"] for m in mets])), 3),
            "centre_minus_border": round(float(np.mean([m["centre_minus_border"] for m in mets])), 3),
        },
    )
    if score < 0.25:
        res.flag("flat_surface", "high", "Face appears planar (photo or screen) rather than three-dimensional.")
    return res
