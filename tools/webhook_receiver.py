#!/usr/bin/env python3
"""Tiny example webhook receiver that VERIFIES the ZeroSpoof signature. Copy its verify() into your own backend.

    python tools/webhook_receiver.py --secret whsec_xxx --port 9001

Register it:  curl -X POST localhost:8000/v1/webhooks -H "X-API-Key: zs_live_dev" -H "Content-Type: application/json" \
                   -d '{"url":"http://localhost:9001/hook","events":["session.failed","session.review","session.passed"]}'
(the response contains the signing secret, shown only once)
"""
import argparse
import hashlib
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def verify(secret: str, header: str, body: bytes, tolerance_s: int = 300) -> bool:
    """Header looks like  t=1700000000,v1=<hex hmac-sha256 of '<t>.<body>'>.  Reject stale timestamps (replay)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
        expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        return abs(time.time() - t) <= tolerance_s and hmac.compare_digest(parts["v1"], expected)
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", required=True)
    ap.add_argument("--port", type=int, default=9001)
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            ok = verify(a.secret, self.headers.get("X-ZeroSpoof-Signature", ""), body)
            ev = json.loads(body)
            print(f"[{'VALID' if ok else 'INVALID SIGNATURE'}] {ev['type']}  session={ev['data']['id']}  verdict={ev['data']['verdict']}  flags={ev['data']['flags']}", flush=True)
            self.send_response(200 if ok else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    print(f"listening on :{a.port}")
    HTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
