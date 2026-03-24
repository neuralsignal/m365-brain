"""Central structlog configuration. Call configure_logging() once at startup."""

from __future__ import annotations

import logging

import structlog


def configure_logging(log_level: str, json_output: bool) -> None:
    """Configure structlog processors and renderer for the entire application.

    Args:
        log_level: Standard Python log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: When True, render logs as JSON (daemon/production). When False,
            use ConsoleRenderer for human-readable output (dev/interactive).
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if json_output:
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
