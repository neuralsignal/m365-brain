"""Tests for m365_brain.logging_config — configure_logging() coverage."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
import structlog

from m365_brain.logging_config import _stderr_logger, configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:  # noqa: PT004
    """Reset structlog to defaults after each test."""
    yield
    structlog.reset_defaults()


class TestConfigureLoggingJsonOutput:
    """json_output=True path."""

    def test_processor_chain_includes_json_renderer(self) -> None:
        configure_logging("INFO", json_output=True)
        cfg = structlog.get_config()
        processor_types = [type(p) for p in cfg["processors"]]
        assert structlog.processors.JSONRenderer in processor_types

    def test_processor_chain_includes_format_exc_info(self) -> None:
        configure_logging("INFO", json_output=True)
        cfg = structlog.get_config()
        assert structlog.processors.format_exc_info in cfg["processors"]

    def test_no_console_renderer(self) -> None:
        configure_logging("INFO", json_output=True)
        cfg = structlog.get_config()
        processor_types = [type(p) for p in cfg["processors"]]
        assert structlog.dev.ConsoleRenderer not in processor_types


class TestConfigureLoggingConsoleOutput:
    """json_output=False path."""

    def test_processor_chain_includes_console_renderer(self) -> None:
        configure_logging("DEBUG", json_output=False)
        cfg = structlog.get_config()
        processor_types = [type(p) for p in cfg["processors"]]
        assert structlog.dev.ConsoleRenderer in processor_types

    def test_no_json_renderer(self) -> None:
        configure_logging("DEBUG", json_output=False)
        cfg = structlog.get_config()
        processor_types = [type(p) for p in cfg["processors"]]
        assert structlog.processors.JSONRenderer not in processor_types

    def test_the_exception_formatter_is_pinned(self) -> None:
        """Left to its default, the renderer prints every frame local.

        `ConsoleRenderer` picks its traceback formatter from whatever happens
        to be importable -- `rich`, then `better-exceptions`, then plain -- and
        the first two print the value of every local in every frame. That
        default is what put 158 ESTS `Cookie` lines and 587 `X-AnchorMailbox`
        lines into a 38 MB daemon log. Neither package is installed here, so
        the argument is asserted rather than the rendering: the day one arrives
        transitively, the choice must already have been taken away.
        """
        with patch("structlog.dev.ConsoleRenderer") as renderer_cls:
            configure_logging("DEBUG", json_output=False)

        assert renderer_cls.call_args.kwargs["exception_formatter"] is structlog.dev.plain_traceback


class TestConfigureLoggingSetsLogLevel:
    """Wrapper class filters at the correct level."""

    @pytest.mark.parametrize(
        ("level_name", "expected_numeric"),
        [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ],
    )
    def test_filtering_level(self, level_name: str, expected_numeric: int) -> None:
        configure_logging(level_name, json_output=False)
        cfg = structlog.get_config()
        wrapper = cfg["wrapper_class"]
        # structlog.make_filtering_bound_logger returns a class whose _min_level
        # attribute (or equivalent) matches the requested level.
        # The class name encodes the level: FilteringBoundLogger.
        # We verify by instantiating and checking that suppressed methods are no-ops.
        bound = wrapper(structlog.PrintLogger(), processors=[], context={})
        if expected_numeric > logging.DEBUG:
            assert bound.debug.__name__ == "_nop"
        if expected_numeric <= logging.DEBUG:
            assert bound.debug.__name__ != "_nop"


class TestConfigureLoggingCommonProcessors:
    """Shared processors present in both branches."""

    @pytest.mark.parametrize("json_output", [True, False])
    def test_common_processors_present(self, json_output: bool) -> None:
        configure_logging("INFO", json_output=json_output)
        cfg = structlog.get_config()
        processors = cfg["processors"]
        processor_types = [type(p) for p in processors]
        assert structlog.contextvars.merge_contextvars in processors
        assert structlog.processors.add_log_level in processors
        assert structlog.processors.StackInfoRenderer in processor_types
        assert structlog.processors.TimeStamper in processor_types

    @pytest.mark.parametrize("json_output", [True, False])
    def test_logger_factory_writes_to_the_current_stderr(self, json_output: bool) -> None:
        configure_logging("INFO", json_output=json_output)
        cfg = structlog.get_config()
        assert cfg["logger_factory"] is _stderr_logger
        assert isinstance(cfg["logger_factory"](), structlog.PrintLogger)
