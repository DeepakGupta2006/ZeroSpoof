import json
import time

import pytest
from fastapi.testclient import TestClient

from zerospoof import synth
from zerospoof.main import app
from zerospoof.simclient import session_messages

KEY = {"X-API-Key": "zs_live_dev"}
ADMIN = {"X-API-Key": "zs_admin_dev"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def run_session(client, kind, seed=5, headers=KEY):
    chall = "turn_left"
    r = client.post("/v1/sessions", json={"external_id": f"user-{kind}"}, headers=headers)
    assert r.status_code == 201, r.text
    created = r.json()
    s = synth.generate(kind, seed=seed, challenge=created["challenge"]["type"])
    final = None
    with client.websocket_connect(f"/ws?token={created['client_token']}") as ws:
        for m in session_messages(s, created["challenge"]["nonce"]):
            ws.send_text(json.dumps(m))
            if m["type"] == "hello":
                assert ws.receive_json()["type"] == "ready"
        while True:
            msg = ws.receive_json()
            if msg["type"] == "final":
                final = msg
                break
    return created, final


def test_health(client):
    assert client.get("/health").json()["ok"] is True


def test_auth_required(client):
    assert client.get("/v1/sessions").status_code == 401
    assert client.get("/v1/sessions", headers={"X-API-Key": "nope"}).status_code == 401


def test_genuine_passes_and_replay_fails_end_to_end(client):
    created, final = run_session(client, "genuine")
    assert final["verdict"] == "pass"
    detail = client.get(f"/v1/sessions/{created['session_id']}", headers=KEY).json()
    assert detail["status"] == "complete" and detail["verdict"] == "pass"
    assert detail["fingerprint"]["camera"]
    assert len(detail["timeline"]) >= 1

    created2, final2 = run_session(client, "replay", seed=6)
    assert final2["verdict"] == "fail"
    d2 = client.get(f"/v1/sessions/{created2['session_id']}", headers=KEY).json()
    assert any(f["code"] == "moire_pattern" for f in d2["flags"])


def test_virtual_camera_is_flagged(client):
    created, final = run_session(client, "virtual_cam", seed=8)
    assert final["verdict"] == "fail"
    d = client.get(f"/v1/sessions/{created['session_id']}", headers=KEY).json()
    assert any(f["code"] == "virtual_camera" for f in d["flags"])


def test_token_is_one_time_and_tamper_proof(client):
    created = client.post("/v1/sessions", json={}, headers=KEY).json()
    tok = created["client_token"]
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_text(json.dumps({"type": "hello", "fingerprint": {}}))
        assert ws.receive_json()["type"] == "ready"
    with client.websocket_connect(f"/ws?token={tok}") as ws:  # replay
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4409
    bad = tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")
    with client.websocket_connect(f"/ws?token={bad}") as ws:
        assert ws.receive()["code"] == 4401


def test_sessions_are_private_to_their_key(client):
    created, _ = run_session(client, "photo", seed=9)
    other = client.post("/v1/keys", json={"name": "other customer"}, headers=ADMIN).json()["api_key"]
    assert client.get(f"/v1/sessions/{created['session_id']}", headers={"X-API-Key": other}).status_code == 404
    assert client.get(f"/v1/sessions/{created['session_id']}", headers=ADMIN).status_code == 200
    assert client.post("/v1/keys", json={"name": "x"}, headers=KEY).status_code == 403


def test_protocol_errors_do_not_kill_session(client):
    created = client.post("/v1/sessions", json={}, headers=KEY).json()
    with client.websocket_connect(f"/ws?token={created['client_token']}") as ws:
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_text(json.dumps({"type": "video", "frames": [{"t": 1, "rois": [[1, 2]]}]}))
        assert ws.receive_json()["type"] == "error"
        ws.send_text(json.dumps({"type": "challenge", "nonce": "wrong", "start": 0, "end": 1}))
        assert ws.receive_json()["type"] == "error"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json()["type"] == "pong"


def test_replay_protection_nonce_header(client):
    h = {**KEY, "X-ZS-Nonce": "abc123", "X-ZS-Timestamp": str(int(time.time()))}
    assert client.post("/v1/sessions", json={}, headers=h).status_code == 201
    assert client.post("/v1/sessions", json={}, headers=h).status_code == 409
    old = {**KEY, "X-ZS-Nonce": "zzz", "X-ZS-Timestamp": str(int(time.time()) - 3600)}
    assert client.post("/v1/sessions", json={}, headers=old).status_code == 400


def test_audit_chain_verifies_and_detects_tampering(client):
    created, _ = run_session(client, "genuine", seed=11)
    events = client.get(f"/v1/sessions/{created['session_id']}/audit", headers=KEY).json()["data"]
    assert [e["event"] for e in events][:1] == ["session.created"] and events[-1]["event"] == "session.completed"
    assert client.get("/v1/audit/verify", headers=ADMIN).json()["ok"] is True

    db = app.state.db
    import sqlalchemy as sa
    with pytest.raises(Exception):  # triggers block UPDATE/DELETE
        with db.engine.begin() as c:
            c.exec_driver_sql("UPDATE audit_log SET payload='{}' WHERE id=1")
    # simulate an attacker with raw DB access who drops the trigger and edits a row
    with db.engine.begin() as c:
        c.exec_driver_sql("DROP TRIGGER audit_no_update")
        c.exec_driver_sql("UPDATE audit_log SET payload='{\"forged\":true}' WHERE id=1")
    verdict = client.get("/v1/audit/verify", headers=ADMIN).json()
    assert verdict["ok"] is False and verdict["first_bad_id"] == 1


def test_usage_metering(client):
    u = client.get("/v1/usage", headers=KEY).json()
    assert u["checks"] >= 3 and u["estimated_total_usd"] >= 49


def test_no_face_tracker_never_fails_for_the_challenge_but_never_passes():
    from zerospoof.engine import LiveSession
    s = synth.generate("genuine", seed=3, challenge="turn_left")
    live = LiveSession("ses_x", "k", {"type": "turn_left", "nonce": "n"})
    msgs = session_messages(s, "n")
    msgs[0]["faceTracker"] = False
    for m in msgs:
        t = m.get("type")
        if t == "hello":
            live.add_hello(m)
        elif t == "video":
            live.add_video(m)
        elif t == "audio":
            live.add_audio(m)
        elif t == "challenge":
            live.set_challenge_window(m)
    res = live.analyze(final=True)
    assert not res["modules"]["challenge"]["available"]
    assert res["verdict"] == "review"
