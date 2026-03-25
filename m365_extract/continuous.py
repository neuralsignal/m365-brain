"""Continuous sync loop — runs extractors on their configured intervals.

Extracted from cli.py to keep the CLI module under the 300-line limit.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import structlog

from m365_extract.config import Config
from m365_extract.extractors.errors import ExtractorError
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.state import SyncState
from m365_extract.storage.base import StorageBackend
from m365_extract.sync import EXTRACTORS

log = structlog.get_logger()


def run_continuous(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> None:
    """Run extractors continuously on their configured intervals."""
    log.info("cli.continuous_started")

    last_run: dict[str, float] = {}
    consecutive_auth_failures = 0
    start_time = time.monotonic()
    loop_count = 0

    try:
        while True:
            now = time.time()
            loop_count += 1
            uptime = time.monotonic() - start_time

            # Determine which extractors are due
            extractors_due = []
            for ext_name in names:
                if ext_name not in EXTRACTORS:
                    continue
                _, config_getter, _ = EXTRACTORS[ext_name]
                ext_config = config_getter(config)
                if not ext_config.enabled:
                    continue
                interval_seconds = ext_config.poll_interval_minutes * 60
                if now - last_run.get(ext_name, 0) >= interval_seconds:
                    extractors_due.append(ext_name)

            log.info(
                "cli.continuous_heartbeat",
                loop=loop_count,
                uptime_seconds=round(uptime, 1),
                extractors_due=len(extractors_due),
            )

            try:
                with GraphClient(config.graph, token_provider) as client:
                    for ext_name in extractors_due:
                        module, config_getter, needs_converters = EXTRACTORS[ext_name]
                        ext_config = config_getter(config)

                        log.info("cli.running_extractor", name=ext_name)
                        state = sync_state.load(ext_name)

                        try:
                            if needs_converters:
                                updated_state, count = module.run(client, storage, state, ext_config, config.converters)
                            else:
                                updated_state, count = module.run(client, storage, state, ext_config)
                            sync_state.save(ext_name, updated_state)
                            last_run[ext_name] = time.time()
                            log.info("cli.extractor_done", name=ext_name, items=count)
                        except (GraphApiError, ExtractorError) as exc:
                            log.error("cli.extractor_failed", name=ext_name, error=str(exc))
                            last_run[ext_name] = time.time()

                consecutive_auth_failures = 0
            except GraphApiError as exc:
                consecutive_auth_failures += 1
                log.error(
                    "cli.auth_failure",
                    error=str(exc),
                    consecutive_failures=consecutive_auth_failures,
                    max_failures=config.service.max_consecutive_auth_failures,
                )
                if consecutive_auth_failures >= config.service.max_consecutive_auth_failures:
                    log.critical(
                        "cli.max_auth_failures_reached",
                        consecutive_failures=consecutive_auth_failures,
                    )
                    raise SystemExit(1) from None

            time.sleep(config.service.continuous_poll_seconds)

    except KeyboardInterrupt:
        log.info("cli.continuous_stopped")
