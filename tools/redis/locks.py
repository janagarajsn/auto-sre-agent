"""
Distributed action locks via Redis.

Prevents duplicate remediation actions when multiple agent instances
are running (e.g., two concurrent alerts for the same deployment).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from tools.redis.client import get_redis_client

_LOCK_PREFIX = "sre:lock"
_DEFAULT_TTL = 120  # seconds


@asynccontextmanager
async def action_lock(
    resource: str,
    ttl: int = _DEFAULT_TTL,
) -> AsyncGenerator[bool, None]:
    """
    Async context manager that acquires a distributed lock for `resource`.
    Yields True if lock was acquired, False if already locked.

    Usage:
        async with action_lock("restart:my-pod") as acquired:
            if not acquired:
                return  # another agent instance is handling this
            await do_the_work()
    """
    redis = get_redis_client()
    lock_key = f"{_LOCK_PREFIX}:{resource}"
    lock_value = str(uuid.uuid4())

    acquired = await redis.set(lock_key, lock_value, nx=True, ex=ttl)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            # Only release if we still own the lock
            current = await redis.get(lock_key)
            if current == lock_value:
                await redis.delete(lock_key)
