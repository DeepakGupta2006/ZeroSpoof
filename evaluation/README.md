# Evaluating ZeroSpoof properly

Accuracy alone is a weak metric for spoof detection (attacks are rare and costly, genuine users are common). Report:

| Metric | Meaning |
|---|---|
| **APCER** | Attack presentations wrongly accepted. Report it **per attack type** and quote the worst |
| **BPCER** | Genuine users wrongly rejected |
| **ACER** | (APCER + BPCER) / 2 |
| **EER** | Error rate at the threshold where APCER = BPCER |
| **Latency** | Time per decision. Target under about 500 ms for an incremental update |
| **Cross-dataset** | Train on dataset A, test on dataset B. Detectors often collapse on unseen attacks |

A *review* verdict counts as "not accepted", so it hurts BPCER for genuine users and helps APCER for attacks.

## 1. Synthetic report (validates the pipeline only)

```bash
python evaluation/evaluate.py --n 30 --out evaluation/report.md
python evaluation/evaluate.py --hard --n 30      # weaker pulse, more noise (compression-like)
```

This runs generated sessions through the **real** pipeline. It proves the plumbing works and gives a latency number. It says **nothing** about real-world accuracy, because the fakes come from the same author as the detectors.

## 2. Real datasets

1. Get the datasets (licences and request forms apply): Replay-Attack, CASIA-FASD, OULU-NPU (screen/print), FaceForensics++, Celeb-DF, DFDC (deepfakes), UBFC-rPPG, PURE (pulse), ASVspoof 2019/2021, WaveFake, In-the-Wild (audio).
2. Score every clip. Use OpenCV 4.x (`pip install "opencv-python-headless>=4.9,<5"`):

   ```bash
   python tools/video_to_session.py clip.mp4 --label attack --attack-type replay \
       --scores-csv scores_replayattack.csv --modules-csv modules_replayattack.csv
   ```

   Loop over your manifest in a shell or Python script. Genuine clips use `--label bonafide`.
3. Report: `python evaluation/evaluate.py --scores scores_replayattack.csv`

Offline mode has no browser fingerprint or challenge, so `fingerprint`, `challenge` and `depth` (unless you installed a depth model) are skipped. The fusion still works on the remaining modules.

## 3. Train the fusion weights and test across datasets

```bash
python evaluation/train_fusion.py --csv modules_replayattack.csv --out /tmp/fusion_A.json   # train on A
# score dataset B with those weights: copy to inference/models/fusion.json, re-run step 2 on B
python evaluation/evaluate.py --scores scores_oulu.csv                                       # test on B
```

Keep a table of A→B results. Large drops are the finding, not a failure of the report.

## 4. Harden and re-test

- Re-encode clips at low bitrates (Zoom/Meet-like) and re-score: rPPG degrades on compression.
- Measure BPCER separately by skin tone, lighting, and camera. Document any gap.
- Try adversarial perturbations and the newest face-swap tools (for example Deep-Live-Cam through OBS Virtual Camera) against the live demo.
