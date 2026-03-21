"""
LangGraph Redis checkpointer factory.

LangGraph uses a checkpointer to persist graph state between node executions.
This enables:
- Graph resumption after human-in-the-loop interrupts
- Fault-tolerant execution (resume after crash)
- State inspection / debugging
"""

from __future__ import annotations

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from configs.settings import get_settings


from contextlib import asynccontextmanager
from typing import AsyncGenerator


@asynccontextmanager
async def build_checkpointer() -> AsyncGenerator[AsyncRedisSaver, None]:
    """
    Async context manager that yields an initialised Redis checkpointer.

    Usage:
        async with build_checkpointer() as checkpointer:
            graph = build_sre_graph(checkpointer=checkpointer)
    """
    settings = get_settings()
    async with AsyncRedisSaver.from_conn_string(str(settings.redis_url)) as checkpointer:
        await checkpointer.asetup()
        yield checkpointer
