"""Dry-run probe logic — validates auth and probes each extractor's Graph endpoint.

Extracted from cli.py to keep the CLI module under the 300-line limit.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog

from m365_extract.config import Config
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.sync import EXTRACTORS

log = structlog.get_logger()

# Maps extractor names to a lightweight Graph probe endpoint.
# Each returns a small payload to confirm the scope is granted.
_DRY_RUN_PROBES: dict[str, str] = {
    "email": "/me/mailFolders/Inbox/messages?$top=1&$select=id,subject",
    # calendar probe computed dynamically — see _dry_run_probe_path()
    "teams_chats": "/me/chats?$top=1&$select=id,topic",
    "teams_channels": "/me/joinedTeams?$select=id,displayName",
    "onedrive": "/me/drive/root/children?$top=1&$select=id,name",
    "sharepoint": "/me/followedSites?$top=1&$select=id,displayName",
    "contacts": "/me/contacts?$top=1&$select=id,displayName",
    "directory": "/users?$top=1&$select=id,displayName",
}


def _dry_run_probe_path(ext_name: str) -> str | None:
    """Return the Graph probe URL for a given extractor, or None.

    Calendar uses a dynamic +-30 day window because Graph rejects
    calendarView ranges exceeding 1825 days.
    """
    if ext_name == "calendar":
        now = datetime.now(tz=UTC)
        start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        end = (now + timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        return f"/me/calendarView?$top=1&$select=id,subject&startDateTime={start}&endDateTime={end}"
    return _DRY_RUN_PROBES.get(ext_name)


def dry_run(config: Config, token_provider: Callable[[], str], names: list[str]) -> None:
    """Validate auth and probe each extractor's endpoint without writing files."""
    log.info("cli.dry_run_start")

    # Step 1: Validate token by calling /me
    with GraphClient(config.graph, token_provider) as client:
        try:
            me = client.get("/me?$select=displayName,userPrincipalName", params=None)
            display_name = me.get("displayName", "unknown")
            upn = me.get("userPrincipalName", "unknown")
            log.info("cli.dry_run_auth_ok", user=display_name, upn=upn)
        except GraphApiError as exc:
            log.error("cli.dry_run_auth_failed", error=str(exc))
            raise SystemExit(1) from exc

        # Step 2: Probe each enabled extractor
        passed = 0
        failed = 0
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("cli.dry_run_probe_unknown", name=ext_name)
                failed += 1
                continue

            _, config_getter, _ = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            if not ext_config.enabled:
                log.info("cli.dry_run_probe_skipped", name=ext_name, reason="disabled")
                continue

            probe_path = _dry_run_probe_path(ext_name)
            if probe_path is None:
                log.info("cli.dry_run_probe_skipped", name=ext_name, reason="no probe configured")
                continue

            try:
                data = client.get(probe_path, params=None)
                item_count = len(data.get("value", []))
                log.info("cli.dry_run_probe_ok", name=ext_name, items=item_count)
                passed += 1
            except GraphApiError as exc:
                log.error("cli.dry_run_probe_failed", name=ext_name, error=str(exc))
                failed += 1

    log.info("cli.dry_run_complete", passed=passed, failed=failed)
    if failed > 0:
        raise SystemExit(1)
