"""Central structlog configuration. Call configure_logging() once at startup.

**Logs go to stderr.** Results go to stdout, and the two must not mix: every
read verb offers `--json`, and a caller piping that into a parser cannot be
made to separate log lines from data first.

**Rendered exceptions never carry frame locals.** `ConsoleRenderer` picks its
traceback formatter from what happens to be installed -- `rich` if present,
else `better-exceptions`, else `traceback` -- and the first two print the value
of every local in every frame. In a daemon that is not a debugging nicety: one
MSAL failure writes the ESTS session cookie, the `X-AnchorMailbox` header
carrying the user's object and tenant ids, and the `grant_type=refresh_token`
POST body straight to disk, truncated only by console width. 94 such failures
put 158 `Cookie` lines and 587 `X-AnchorMailbox` lines into a 38 MB log. Pinning
`plain_traceback` removes the choice, so no caller can configure this library
into leaking regardless of what else is in the environment.

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


def route_logs_to_stderr() -> None:
    """Point structlog at stderr before anything can log.

    The invariant at the top of this module was true of `configure_logging`
    and false of the process, because only two verbs called it. Every other
    command ran on structlog's default factory, which writes to **stdout** --
    so `outbox list --json` emitted 54 warning lines ahead of its JSON and
    `json.load` raised. The output was documented as machine-readable and was
    not parseable at all.

    This sets the destination only. Level and renderer still come from config
    via `configure_logging`, which cannot happen this early: `--config` is
    optional at the group level so that `init` can create the file it names.
    The gap between process start and that call is exactly where the stray
    lines came from, so the floor is set here and refined later.
    """
    structlog.configure(logger_factory=_stderr_logger)


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
        processors.append(structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level),
        ),
        context_class=dict,
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )
