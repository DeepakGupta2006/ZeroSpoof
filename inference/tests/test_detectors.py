import numpy as np
import pytest

from zerospoof import fusion, synth
from zerospoof.detectors import audio, challenge, depth, fingerprint, moire, rppg


def _arrays(s):
    t = np.array([f["t"] for f in s.frames])
    r = np.array([f["rois"] for f in s.frames])
    return t, r


def test_rppg_finds_pulse_in_genuine_but_not_photo():
    g = synth.generate("genuine", seed=1)
    p = synth.generate("photo", seed=1)
    rg = rppg.analyze(*_arrays(g))
    rp = rppg.analyze(*_arrays(p))
    assert rg.score > 0.6 and 50 < rg.details["bpm"] < 110
    assert rp.score < 0.3
    assert rg.score > rp.score + 0.4


def test_rppg_needs_enough_video():
    g = synth.generate("genuine", duration=5, seed=2)
    assert not rppg.analyze(*_arrays(g)).available


def test_rppg_flags_incoherent_regions_of_deepfake():
    scores = [rppg.analyze(*_arrays(synth.generate("deepfake", seed=s))).score for s in range(5)]
    assert max(scores) < 0.5


def test_moire_detects_screen_and_print():
    for kind in ("replay", "photo"):
        s = synth.generate(kind, seed=3)
        r = moire.analyze([p["data"] for p in s.patches])
        assert r.score < 0.3, (kind, r.details)
    g = synth.generate("genuine", seed=3)
    assert moire.analyze([p["data"] for p in g.patches]).score > 0.7


def test_depth_relief_metrics_separate_plane_from_face():
    yy, xx = np.mgrid[0:64, 0:64]
    plane = 0.3 * xx + 0.2 * yy + np.random.default_rng(0).normal(0, 0.01, (64, 64))
    bump = np.exp(-(((xx - 32) / 14) ** 2 + ((yy - 32) / 18) ** 2))
    assert depth.score_from_metrics(depth.relief_metrics(plane)) < 0.1
    assert depth.score_from_metrics(depth.relief_metrics(bump)) > 0.9
    # without a model file the module must say so, not guess
    assert not depth.analyze([np.zeros((128, 128), np.uint8)] * 5).available


def test_audio_flags_spliced_synthetic_voice():
    good = audio.analyze(synth._voice(np.random.default_rng(0), 8, "genuine"))
    bad = audio.analyze(synth._voice(np.random.default_rng(0), 8, "voice_clone"))
    assert good.score > 0.7 and bad.score < 0.4
    assert bad.confidence < 0.5  # heuristic tier is deliberately low-confidence
    assert not audio.analyze(None).available
    assert not audio.analyze(np.zeros(16000 * 4, np.float32)).available  # silence


def test_fingerprint_virtual_camera_and_tampering():
    fp = synth._fingerprint(np.random.default_rng(0), "genuine")
    assert fingerprint.client_checks(fp).score == 1.0
    fp["activeTrack"]["label"] = "OBS Virtual Camera"
    r = fingerprint.client_checks(fp)
    assert r.score < 0.2 and any(f["code"] == "virtual_camera" for f in r.flags)
    fp2 = synth._fingerprint(np.random.default_rng(0), "genuine")
    fp2["nativeApis"]["getUserMedia"] = False
    assert any(f["code"] == "api_tampering" for f in fingerprint.client_checks(fp2).flags)


def test_stream_checks_catch_loop_and_regular_timing():
    s = synth.generate("virtual_cam", seed=4)
    t, r = _arrays(s)
    res = fingerprint.stream_checks(t, r, [p["data"] for p in s.patches])
    codes = {f["code"] for f in res.flags}
    assert "looped_video" in codes and "too_regular_timing" in codes
    g = synth.generate("genuine", seed=4)
    t, r = _arrays(g)
    assert fingerprint.stream_checks(t, r, [p["data"] for p in g.patches]).score > 0.9


def test_challenge_evaluation():
    t = np.arange(0, 4000, 33.3)
    yaw = np.zeros_like(t)
    blink = np.zeros_like(t)
    ch = {"type": "turn_left", "nonce": "n"}
    assert challenge.evaluate(ch, t, yaw, blink, (0, 4000)).score == 0.0
    yaw[:] = -25 * np.sin(np.linspace(0, np.pi, len(t)))
    assert challenge.evaluate(ch, t, yaw, blink, (0, 4000)).score == 1.0
    ch2 = {"type": "turn_right", "nonce": "n"}
    assert challenge.evaluate(ch2, t, yaw, blink, (0, 4000)).score == 0.0  # wrong direction
    ch3 = {"type": "blink_twice", "nonce": "n"}
    blink[20:24] = 0.9
    blink[60:64] = 0.9
    assert challenge.evaluate(ch3, t, np.zeros_like(t), blink, (0, 4000)).score == 1.0


@pytest.mark.parametrize("kind,expected", [("genuine", {"pass"}), ("replay", {"fail"}), ("photo", {"fail"}),
                                           ("virtual_cam", {"fail"}), ("voice_clone", {"review", "fail"}),
                                           ("deepfake", {"review", "fail"})])
def test_fusion_verdicts_on_synthetic_sessions(kind, expected):
    from zerospoof.engine import LiveSession
    from zerospoof.simclient import session_messages
    s = synth.generate(kind, seed=7, challenge="turn_right")
    live = LiveSession("s", "k", {"type": "turn_right", "nonce": "N"})
    for m in session_messages(s, "N"):
        if m["type"] == "hello":
            live.add_hello(m)
        elif m["type"] == "video":
            live.add_video(m)
        elif m["type"] == "audio":
            live.add_audio(m)
        elif m["type"] == "challenge":
            live.set_challenge_window(m)
    res = live.analyze(final=True)
    assert res["verdict"] in expected, res
    assert res["latency_ms"]["last"] < 2000


def test_fusion_never_passes_without_independent_evidence():
    from zerospoof.detectors import ModuleResult
    mods = {"fingerprint": ModuleResult("fingerprint", 1.0, 0.7), "audio": ModuleResult("audio", 1.0, 0.9)}
    assert fusion.decide(mods, 20, True).verdict != "pass"


def test_rppg_ignores_head_turn_motion():
    """A real pulse must survive a head-turn: the analysis uses the still part of the session."""
    s = synth.generate("genuine", seed=5, challenge="turn_left", hard=True)
    t = np.array([f["t"] for f in s.frames])
    r = np.array([f["rois"] for f in s.frames])
    yaw = np.array([f["yaw"] for f in s.frames])
    w = s.challenge_window
    m = (t >= w[0] - 300) & (t <= w[1] + 300)
    rng = np.random.default_rng(1)
    r = r.copy()
    r[m] += np.cumsum(rng.normal(0, 1.2, (m.sum(), 1, 1)), axis=0) * np.array([1, 0.8, 0.6]) + rng.normal(0, 2.0, r[m].shape)
    gated = rppg.analyze(t, r, yaw=yaw, skip_window=w)
    assert gated.score > 0.4
    assert gated.details["segment"] == "still segment"


def test_demo_relax_lets_a_good_session_pass_without_pulse(monkeypatch):
    from zerospoof.config import settings
    from zerospoof.detectors import ModuleResult
    mods = {
        "rppg": ModuleResult("rppg", 0.0, 1.0),
        "stream": ModuleResult("stream", 1.0, 0.7),
        "challenge": ModuleResult("challenge", 1.0, 0.8),
        "moire": ModuleResult("moire", 0.94, 1.0),
        "audio": ModuleResult("audio", 1.0, 0.3),
        "fingerprint": ModuleResult("fingerprint", 1.0, 0.7),
    }
    monkeypatch.setattr(settings, "demo_relax", False)
    assert fusion.decide(mods, 16.0, True).verdict == "review"
    monkeypatch.setattr(settings, "demo_relax", True)
    assert fusion.decide(mods, 16.0, True).verdict == "pass"
