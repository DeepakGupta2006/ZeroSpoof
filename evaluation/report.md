# ZeroSpoof PAD report (SYNTHETIC data: validates the pipeline, NOT real-world accuracy)

bona fide samples: 30   attack samples: 150

Operating point (accept if p >= 0.70):
  BPCER = 0.0%
  APCER (worst attack type) = 13.3%
  ACER  = 6.7%
  EER   = 0.0% at p = 0.99

APCER per attack type:
  deepfake     13.3%
  photo        0.0%
  replay       0.0%
  virtual_cam  0.0%
  voice_clone  3.3%

Decision latency (analysis of a full session, single CPU thread): median 72 ms, p95 89 ms
  (target < ~500 ms for an incremental update; a full 14 s session is the worst case)

Note: with the built-in heuristics, 'voice_clone' and 'deepfake' mostly land in REVIEW (counted as not-accepted), but a few are still accepted (non-zero APCER above).
These are the hardest attacks; real detection quality needs trained models and real datasets (evaluation/README.md).
