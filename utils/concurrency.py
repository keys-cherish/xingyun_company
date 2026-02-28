"""Concurrency control utilities."""

from __future__ import annotations

import functools
from collections.abc import Callable

from cache.redis_client import RedisLock


def with_lock(key_template: str):
    """Decorator that wraps an async function with a Redis distributed lock.

    Usage:
        @with_lock("invest:{company_id}")
        async def invest(company_id: int, ...): ...

    The key_template is formatted with the function's keyword arguments.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            lock_key = key_template.format(**kwargs)
            async with RedisLock(lock_key):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
