"""Small async in-memory Redis replacement for unit tests."""

from __future__ import annotations

import time


class FakeRedis:
    """Subset of redis.asyncio API used by this project."""

    def __init__(self):
        self._kv: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def _cleanup_key(self, key: str) -> None:
        expires_at = self._expiry.get(key)
        if expires_at is not None and expires_at <= time.time():
            self._kv.pop(key, None)
            self._expiry.pop(key, None)

    async def get(self, key: str):
        self._cleanup_key(key)
        return self._kv.get(key)

    async def set(self, key: str, value, nx: bool = False, ex: int | None = None):
        self._cleanup_key(key)
        if nx and key in self._kv:
            return False
        self._kv[key] = str(value)
        if ex is not None:
            self._expiry[key] = time.time() + int(ex)
        else:
            self._expiry.pop(key, None)
        return True

    async def setex(self, key: str, ttl_seconds: int, value):
        self._kv[key] = str(value)
        self._expiry[key] = time.time() + int(ttl_seconds)
        return True

    async def exists(self, key: str) -> int:
        self._cleanup_key(key)
        return 1 if key in self._kv else 0

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            self._cleanup_key(key)
            if key in self._kv:
                removed += 1
                self._kv.pop(key, None)
                self._expiry.pop(key, None)
        return removed

    async def ttl(self, key: str) -> int:
        self._cleanup_key(key)
        if key not in self._kv:
            return -2
        expires_at = self._expiry.get(key)
        if expires_at is None:
            return -1
        remain = int(expires_at - time.time())
        if remain <= 0:
            self._kv.pop(key, None)
            self._expiry.pop(key, None)
            return -2
        return remain

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self._cleanup_key(key)
        if key not in self._kv:
            return False
        self._expiry[key] = time.time() + int(ttl_seconds)
        return True

    async def incrby(self, key: str, amount: int) -> int:
        self._cleanup_key(key)
        current = int(self._kv.get(key, "0"))
        new_val = current + int(amount)
        self._kv[key] = str(new_val)
        return new_val

    async def incr(self, key: str) -> int:
        return await self.incrby(key, 1)

    async def eval(self, _script: str, _numkeys: int, key: str, amount: int):
        current = int((await self.get(key)) or 0)
        amount_i = int(amount)
        if current < amount_i:
            return -1
        return await self.incrby(key, -amount_i)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            z[str(member)] = float(score)
        return len(mapping)

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
        z = self._zsets.get(key, {})
        ranked = sorted(z.items(), key=lambda item: item[1], reverse=True)

        real_end = len(ranked) - 1 if end == -1 else end
        sliced = ranked[start: real_end + 1]
        if withscores:
            return sliced
        return [member for member, _ in sliced]

    async def aclose(self):
        self._kv.clear()
        self._expiry.clear()
        self._zsets.clear()
