"""
FastAPI application factory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from api.middleware.logging import LoggingMiddleware
from api.routes import alerts, approvals, health, incidents
from observability.logging import configure_logging, get_logger
from observability.metrics import REGISTRY
from observability.tracing import configure_tracing
from tools.base import register_all_tools

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_tracing()
    register_all_tools()
    logger.info("auto-sre-agent started")
    yield
    logger.info("auto-sre-agent shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="auto-sre-agent",
        description="Autonomous SRE agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(LoggingMiddleware)

    app.include_router(health.router, tags=["health"])
    app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
    app.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
    app.include_router(incidents.router, prefix="/incidents", tags=["incidents"])

    # Expose Prometheus metrics on /metrics
    metrics_app = make_asgi_app(registry=REGISTRY)
    app.mount("/metrics", metrics_app)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    from configs.settings import get_settings

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "dev",
    )
