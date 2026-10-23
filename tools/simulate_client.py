#!/usr/bin/env python3
"""Pretend to be the browser SDK: stream synthetic sessions to a running ZeroSpoof server.

Lets you see the dashboard fill up and test the Go gateway end to end WITHOUT a camera.

    python tools/simulate_client.py                       # one session of every kind
    python tools/simulate_client.py --kinds genuine,replay --repeat 3
    python tools/simulate_client.py --url http://localhost:8000 --key zs_live_dev
    python tools/simulate_client.py --realtime            # pace messages like a real camera (14 s per session)

Needs:  pip install websockets httpx numpy scipy   (and run from the repo root)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "inference"))

import httpx  # noqa: E402
import websockets  # noqa: E402

from zerospoof import synth  # noqa: E402
from zerospoof.simclient import session_messages  # noqa: E402


async def run_one(base: str, key: str, kind: str, seed: int, realtime: bool) -> dict:
    async with httpx.AsyncClient(base_url=base, headers={"X-API-Key": key}, timeout=30) as http:
        r = await http.post("/v1/sessions", json={"external_id": f"sim-{kind}-{seed}"})
        r.raise_for_status()
        created = r.json()
    chall = created["challenge"]
    s = synth.generate(kind, seed=seed, challenge=chall["type"] if chall else "turn_left")
    msgs = session_messages(s, chall["nonce"] if chall else None)
    url = f"{created['ws_url']}?token={created['client_token']}"
    t0 = time.time()
    final = {}
    async with websockets.connect(url, max_size=2**22) as ws:
        async def reader():
            nonlocal final
            async for raw in ws:
                m = json.loads(raw)
                if m["type"] == "final":
                    final = m
                    return
                if m["type"] == "error":
                    print("   server error:", m["message"])
        rt = asyncio.create_task(reader())
        for m in msgs:
            await ws.send(json.dumps(m))
            if realtime and m["type"] in ("video", "audio"):
                await asyncio.sleep(0.1)  # video + audio message per 0.2 s of media
        await asyncio.wait_for(rt, timeout=60)
    return {"kind": kind, "session_id": created["session_id"], "challenge": chall["type"] if chall else None,
            "verdict": final.get("verdict"), "p": final.get("probability"), "secs": round(time.time() - t0, 1),
            "flags": [f["code"] for f in final.get("flags", [])]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000", help="inference REST base URL")
    ap.add_argument("--key", default="zs_live_dev")
    ap.add_argument("--kinds", default=",".join(synth.KINDS))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--realtime", action="store_true")
    a = ap.parse_args()

    print(f"{'kind':12s} {'verdict':8s} {'p':>5s}  flags")
    for rep in range(a.repeat):
        for i, kind in enumerate(a.kinds.split(",")):
            res = await run_one(a.url, a.key, kind.strip(), 100 * rep + i + int(time.time()) % 1000, a.realtime)
            p = f"{res['p']:.2f}" if res["p"] is not None else "  - "
            print(f"{res['kind']:12s} {str(res['verdict']):8s} {p:>5s}  {', '.join(res['flags'][:4])}")


if __name__ == "__main__":
    asyncio.run(main())
