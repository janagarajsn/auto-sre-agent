"""
Conversation memory store for injecting historical incident context into the LLM.
Stores summarised incident context, not raw chat messages (that's the checkpointer's job).
"""

from __future__ import annotations

from configs.settings import get_settings
from tools.redis.client import get_redis_client

_KEY = "sre:context:recent_incidents"


async def push_incident_summary(summary: str) -> None:
    redis = get_redis_client()
    await redis.lpush(_KEY, summary)
    await redis.ltrim(_KEY, 0, 9)  # Keep last 10
    await redis.expire(_KEY, get_settings().redis_ttl_seconds)


async def get_recent_summaries(limit: int = 5) -> list[str]:
    redis = get_redis_client()
    return await redis.lrange(_KEY, 0, limit - 1)
