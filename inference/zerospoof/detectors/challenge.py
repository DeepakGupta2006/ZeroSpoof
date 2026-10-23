"""Challenge-response liveness.

The server picks a random challenge (turn head left/right, or blink twice) at session creation and
sends it to the browser. A pre-recorded replay, a printed photo or a looped virtual-camera clip
cannot react to a challenge it has never seen. A *live* face-swap attacker can, so this module is
only one signal; rPPG and artifact checks cover that case.

Convention: the browser reports `yaw` in degrees, negative = user turned to their LEFT.
It reports `blink` as a 0..1 eye-closure score (MediaPipe eyeBlink blendshape average).
"""
from __future__ import annotations

import secrets
from typing import Any

import numpy as np

from . import ModuleResult, unavailable

CHALLENGES = ["turn_left", "turn_right", "blink_twice"]
YAW_REQUIRED_DEG = 14.0


def new_challenge() -> dict[str, Any]:
    return {"type": secrets.choice(CHALLENGES), "nonce": secrets.token_hex(8)}


def _blink_count(blink: np.ndarray, thr: float = 0.5) -> int:
    above = blink > thr
    return int(np.sum(above[1:] & ~above[:-1]) + (1 if above[0] else 0))


def evaluate(challenge: dict[str, Any] | None, t_ms: np.ndarray, yaw: np.ndarray, blink: np.ndarray,
             window: tuple[float, float] | None) -> ModuleResult:
    """window = (start_ms, end_ms) in client time, reported by the client when the challenge ran."""
    name = "challenge"
    if not challenge:
        return unavailable(name, "no challenge issued")
    if window is None:
        return unavailable(name, "challenge not performed yet")
    start, end = window
    m = (t_ms >= start) & (t_ms <= end)
    if m.sum() < 15:
        return ModuleResult(name, score=0.0, confidence=0.7, details={"reason": "no video during challenge window", "type": challenge["type"]})

    kind = challenge["type"]
    y = yaw[m]
    b = blink[m]
    details: dict[str, Any] = {"type": kind}
    passed = False
    if kind in ("turn_left", "turn_right"):
        sign = -1.0 if kind == "turn_left" else 1.0
        reached = float(np.max(sign * y)) if len(y) else 0.0
        opposite = float(np.max(-sign * y)) if len(y) else 0.0
        details.update({"max_yaw_in_direction": round(reached, 1), "max_yaw_opposite": round(opposite, 1)})
        passed = reached >= YAW_REQUIRED_DEG and reached > opposite + 4.0
    else:
        n = _blink_count(b)
        details["blinks"] = n
        passed = n >= 2

    res = ModuleResult(name, score=1.0 if passed else 0.0, confidence=0.8, details=details)
    if not passed:
        res.flag("challenge_failed", "high", f"User did not perform the random challenge ({kind}).")
    return res
