"""
Redis async client factory (singleton per process).
"""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as aioredis

from configs.settings import get_settings


@lru_cache(maxsize=1)
def get_redis_client() -> aioredis.Redis:
    settings = get_settings()
    return aioredis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
