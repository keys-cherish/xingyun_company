"""Redis async client, distributed lock, and leaderboard helpers."""

from __future__ import annotations

import redis.asyncio as aioredis

from config import settings

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _pool


async def close_redis():
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ---------- Distributed lock helper ----------

class RedisLock:
    """Simple async Redis lock using SET NX EX."""

    def __init__(self, key: str, timeout: int = 10):
        self.key = f"lock:{key}"
        self.timeout = timeout
        self._redis: aioredis.Redis | None = None

    async def __aenter__(self):
        self._redis = await get_redis()
        while True:
            acquired = await self._redis.set(self.key, "1", nx=True, ex=self.timeout)
            if acquired:
                return self
            import asyncio
            await asyncio.sleep(0.05)

    async def __aexit__(self, *args):
        if self._redis:
            await self._redis.delete(self.key)


# ---------- Leaderboard helpers ----------

async def update_leaderboard(board: str, member: str, score: float):
    r = await get_redis()
    await r.zadd(f"lb:{board}", {member: score})


async def get_leaderboard(board: str, top_n: int = 10) -> list[tuple[str, float]]:
    r = await get_redis()
    return await r.zrevrange(f"lb:{board}", 0, top_n - 1, withscores=True)
