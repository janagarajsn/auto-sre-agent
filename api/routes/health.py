"""
Health check endpoints for Kubernetes liveness and readiness probes.
"""

from __future__ import annotations

from fastapi import APIRouter

from tools.redis.client import get_redis_client

router = APIRouter()


@router.get("/healthz")
async def liveness() -> dict:
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness() -> dict:
    """
    Readiness probe — verifies Redis connectivity.
    Returns 503 if dependencies are unavailable.
    """
    redis = get_redis_client()
    try:
        await redis.ping()
        return {"status": "ready", "redis": "ok"}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}")
