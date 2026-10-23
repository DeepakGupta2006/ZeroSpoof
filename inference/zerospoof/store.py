"""Session state store: Redis in production, in-memory for zero-setup local dev.

What lives here: live session metadata, rolling score windows, one-time token nonces,
and rate-limit counters. (Raw frame buffers stay in the worker process that owns the WebSocket;
a WebSocket is sticky to one worker anyway.)
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

log = logging.getLogger("zerospoof.store")

SESSION_TTL = 24 * 3600
SCORE_WINDOW = 120  # keep the last N score points per session


class MemoryStore:
    kind = "memory"

    def __init__(self) -> None:
        self._kv: dict[str, tuple[float | None, Any]] = {}
        self._scores: dict[str, deque] = defaultdict(lambda: deque(maxlen=SCORE_WINDOW))
        self._counters: dict[str, tuple[float, int]] = {}

    def _alive(self, key: str) -> bool:
        item = self._kv.get(key)
        if item is None:
            return False
        exp, _ = item
        if exp is not None and exp < time.time():
            del self._kv[key]
            return False
        return True

    async def set_json(self, key: str, value: Any, ttl: int | None = SESSION_TTL) -> None:
        self._kv[key] = (time.time() + ttl if ttl else None, json.loads(json.dumps(value)))

    async def get_json(self, key: str) -> Any | None:
        return self._kv[key][1] if self._alive(key) else None

    async def setnx(self, key: str, ttl: int) -> bool:
        """True if the key was created (first use), False if it already existed."""
        if self._alive(key):
            return False
        self._kv[key] = (time.time() + ttl, 1)
        return True

    async def incr_window(self, key: str, window_s: int) -> int:
        now = time.time()
        start, n = self._counters.get(key, (now, 0))
        if now - start >= window_s:
            start, n = now, 0
        n += 1
        self._counters[key] = (start, n)
        return n

    async def push_score(self, sid: str, point: dict[str, Any]) -> None:
        self._scores[sid].append(point)

    async def get_scores(self, sid: str) -> list[dict[str, Any]]:
        return list(self._scores.get(sid, []))

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class RedisStore:
    kind = "redis"

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self.r = redis.from_url(url, decode_responses=True)

    async def set_json(self, key: str, value: Any, ttl: int | None = SESSION_TTL) -> None:
        await self.r.set(f"zs:{key}", json.dumps(value), ex=ttl)

    async def get_json(self, key: str) -> Any | None:
        raw = await self.r.get(f"zs:{key}")
        return json.loads(raw) if raw else None

    async def setnx(self, key: str, ttl: int) -> bool:
        return bool(await self.r.set(f"zs:{key}", "1", nx=True, ex=ttl))

    async def incr_window(self, key: str, window_s: int) -> int:
        k = f"zs:rl:{key}:{int(time.time() // window_s)}"
        pipe = self.r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_s * 2)
        n, _ = await pipe.execute()
        return int(n)

    async def push_score(self, sid: str, point: dict[str, Any]) -> None:
        k = f"zs:scores:{sid}"
        pipe = self.r.pipeline()
        pipe.rpush(k, json.dumps(point))
        pipe.ltrim(k, -SCORE_WINDOW, -1)
        pipe.expire(k, SESSION_TTL)
        await pipe.execute()

    async def get_scores(self, sid: str) -> list[dict[str, Any]]:
        return [json.loads(x) for x in await self.r.lrange(f"zs:scores:{sid}", 0, -1)]

    async def ping(self) -> bool:
        try:
            return bool(await self.r.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.r.aclose()


async def make_store(redis_url: str):
    if redis_url:
        s = RedisStore(redis_url)
        if await s.ping():
            log.info("using Redis store")
            return s
        log.warning("REDIS_URL set but Redis is unreachable; falling back to in-memory store")
    return MemoryStore()
