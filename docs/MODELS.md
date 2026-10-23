# Models and training

ZeroSpoof runs with **no model files**. Everything below is an upgrade path, in the order that gives the most value.

## 1. Fusion weights (`inference/models/fusion.json`)

The defaults are hand-set. To learn them from data:

1. Score labelled videos with `python tools/video_to_session.py clip.mp4 --label bonafide|attack --attack-type replay --modules-csv modules.csv` (append one row per clip).
2. `python evaluation/train_fusion.py --csv modules.csv` writes `inference/models/fusion.json`.
3. Restart the inference service.

Train on one dataset and **test on another**. Never ship weights learned from the synthetic data (`--synthetic` only demonstrates the mechanics).

## 2. Audio anti-spoofing

Order of preference (the first one found is used):

1. `inference/models/audio.onnx`: AASIST or RawNet2 trained on ASVspoof 2019 LA. Input: raw 16 kHz waveform, shape `[1, 64600]` float32. Output: 2 logits `[spoof, bonafide]`. Needs `pip install onnxruntime`.
2. `inference/models/audio_lr.json`: a logistic-regression baseline on interpretable spectral features. Train with `python tools/train_audio_baseline.py data/ --out inference/models/audio_lr.json` where `data/bonafide/*.wav` and `data/spoof/*.wav` hold your corpus (ASVspoof, WaveFake, In-the-Wild).
3. Built-in heuristics (flags `synthetic_voice` and forces human review; it never fails a session on its own).

Evaluate across corpora (train ASVspoof, test In-the-Wild). Voice-spoof detectors often fail on generators they have not seen.

## 3. Depth (photo / mask defence)

`pip install onnxruntime` then `python tools/get_models.py` downloads MiDaS-small ONNX to `inference/models/depth.onnx`. Any monocular depth ONNX with NCHW float32 RGB input works. If the download is blocked, fetch the file by hand and save it under that name.

## 4. rPPG

Ships classical (POS). To go further, replace the estimator in `detectors/rppg.py` with a deep model (PhysNet, TS-CAN) trained on UBFC-rPPG / PURE. The browser already supplies per-region colour traces; a deep model would need face crops at a higher rate.

## Datasets

| Area | Datasets |
|---|---|
| Face and video | FaceForensics++, Celeb-DF, DFDC, CASIA-FASD, Replay-Attack, OULU-NPU |
| rPPG | UBFC-rPPG, PURE |
| Audio | ASVspoof 2019/2021, WaveFake, In-the-Wild |

These have their own licences and access forms. Generate your own fakes too (open-source face-swap and TTS tools), because public benchmarks go stale as new generators appear.
