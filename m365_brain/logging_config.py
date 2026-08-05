"""Central structlog configuration. Call configure_logging() once at startup.

**Logs go to stderr.** Results go to stdout, and the two must not mix: every
read verb offers `--json`, and a caller piping that into a parser cannot be
made to separate log lines from data first.

`sys.stderr` is looked up when a logger is *created*, not when this function
runs. `structlog.PrintLoggerFactory(file=sys.stderr)` captures the stream
object at configuration time, which makes the destination whatever stderr
happened to be during the first `configure_logging` call in the process -- a
global that outlives its caller, and the reason logs escaped capture under
test. Resolving it per logger costs one attribute lookup and removes the
global.
"""

from __future__ import annotations

import logging
import sys

import structlog


def _stderr_logger(*args: object) -> structlog.PrintLogger:
    """A logger bound to whatever `sys.stderr` is right now."""
    return structlog.PrintLogger(file=sys.stderr)


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
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )
