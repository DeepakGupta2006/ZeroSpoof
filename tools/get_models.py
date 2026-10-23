#!/usr/bin/env python3
"""Optional: fetch the depth model (MiDaS-small ONNX) into inference/models/depth.onnx.

ZeroSpoof runs without any model files (the depth module then reports "unavailable"). This helper
tries the official MiDaS v2.1 small ONNX release. If your network blocks it, download the file by
hand and save it as inference/models/depth.onnx.

    pip install onnxruntime
    python tools/get_models.py

Audio (AASIST / RawNet2): train on ASVspoof 2019 LA, export to ONNX with a raw-waveform input
(shape [1, 64600], float32, 16 kHz) and a 2-way output ([spoof, bonafide]); save as inference/models/audio.onnx.
See docs/MODELS.md.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "inference" / "models"
URLS = {"depth.onnx": "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx"}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in URLS.items():
        out = DEST / name
        if out.exists():
            print(f"{name}: already present")
            continue
        print(f"downloading {url} ...")
        try:
            urllib.request.urlretrieve(url, out)
            print(f"{name}: saved ({out.stat().st_size / 1e6:.1f} MB)")
        except Exception as e:
            out.unlink(missing_ok=True)
            print(f"{name}: FAILED ({e}).\n  Download it manually from {url}\n  and save it as {out}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
