"""
Structured JSON logging configuration using structlog.
All log output is machine-parseable for ingestion by Loki / CloudWatch / Datadog.
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache

import structlog

from configs.settings import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if sys.stdout.isatty():
        # Human-readable output for local dev
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Suppress noisy third-party loggers
    for lib in ("kubernetes", "urllib3", "httpx", "openai"):
        logging.getLogger(lib).setLevel(logging.WARNING)


@lru_cache(maxsize=None)
def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
