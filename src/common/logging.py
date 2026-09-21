"""Structured logging configuration."""
from __future__ import annotations

import logging
import sys
from typing import Any, Optional

import structlog


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure structured logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Quiet noisy third-party loggers
    for noisy in ("ib_insync", "asyncio", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str, **initial_values: Any) -> structlog.BoundLogger:
    return structlog.get_logger(name, **initial_values)


def bind_trade_context(
    trade_id: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Bind trade context variables for all subsequent log calls in this context."""
    ctx: dict[str, str] = {}
    if trade_id:
        ctx["trade_id"] = trade_id
    if symbol:
        ctx["symbol"] = symbol
    if strategy:
        ctx["strategy"] = strategy
    if correlation_id:
        ctx["correlation_id"] = correlation_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_trade_context() -> None:
    structlog.contextvars.clear_contextvars()
