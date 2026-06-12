"""
Structured observability layer for Django Nova.
Replaces standard string-based logging with machine-readable JSON events.
"""
from __future__ import annotations

import logging

import structlog


def setup_nova_logging() -> None:
    """
    Configures structlog for Django Nova.
    Call this once in Django's AppConfig.ready() or during testing.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Returns a structured logger instance for a specific Nova module.
    Falls back to standard logging if structlog is not configured.
    """
    if structlog.is_configured():
        return structlog.get_logger(name)
    return structlog.stdlib.get_logger(name)
