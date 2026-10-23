"""Turn a SynthSession into the exact WebSocket messages the browser SDK would send."""
from __future__ import annotations

import base64
from typing import Any

import numpy as np

from .synth import SynthSession


def session_messages(s: SynthSession, nonce: str | None, batch_s: float = 0.2) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"type": "hello", "fingerprint": s.fingerprint}]
    frames, patches = s.frames, s.patches
    t0 = frames[0]["t"]
    end = frames[-1]["t"]
    cur = t0
    fi = pi = 0
    audio = s.audio
    sr = 16000
    ai = 0
    while cur <= end:
        nxt = cur + batch_s * 1000
        fb = []
        while fi < len(frames) and frames[fi]["t"] < nxt:
            fb.append(frames[fi])
            fi += 1
        pb = []
        while pi < len(patches) and patches[pi]["t"] < nxt:
            p = patches[pi]
            pb.append({"t": p["t"], "data": base64.b64encode(p["data"].tobytes()).decode()})
            pi += 1
        if fb or pb:
            msgs.append({"type": "video", "frames": fb, "patches": pb})
        if audio is not None:
            n = int(batch_s * sr)
            chunk = audio[ai : ai + n]
            ai += n
            if len(chunk):
                pcm = (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
                msgs.append({"type": "audio", "sr": sr, "pcm": base64.b64encode(pcm).decode()})
        cur = nxt
    if nonce and s.challenge_window:
        msgs.append({"type": "challenge", "nonce": nonce, "start": s.challenge_window[0], "end": s.challenge_window[1]})
    msgs.append({"type": "finish"})
    return msgs
