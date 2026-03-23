"""Background sync scheduler for multi-user mode.

Uses APScheduler's BackgroundScheduler (sync) to periodically run extractors for all enabled users.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from m365_extract.auth.token_provider import make_web_token_provider
from m365_extract.config import Config
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.sync import run_extractors

if TYPE_CHECKING:
    from m365_extract.auth.token_store import TokenStore
    from m365_extract.user_manager import UserManager

log = structlog.get_logger()


class SyncScheduler:
    """Periodic sync scheduler for all enabled users."""

    def __init__(self, config: Config, token_store: TokenStore, user_manager: UserManager) -> None:
        self._config = config
        self._token_store = token_store
        self._user_manager = user_manager
        self._scheduler = BackgroundScheduler()
        self._interval_minutes = self._compute_interval()

    def start(self) -> None:
        """Schedule jobs for all enabled users and start the scheduler."""
        for user in self._user_manager.list_users():
            if user.enabled:
                self.add_user_job(user.user_id)
        self._scheduler.start()
        log.info("scheduler.started", interval_minutes=self._interval_minutes)

    def shutdown(self) -> None:
        """Shut down the scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        log.info("scheduler.shutdown")

    def add_user_job(self, user_id: str) -> None:
        """Add a periodic sync job for a user."""
        job_id = f"sync_{user_id}"
        self._scheduler.add_job(
            self._sync_user,
            "interval",
            minutes=self._interval_minutes,
            args=[user_id],
            id=job_id,
            replace_existing=True,
        )
        log.info("scheduler.job_added", user_id=user_id, interval_minutes=self._interval_minutes)

    def remove_user_job(self, user_id: str) -> None:
        """Remove a user's sync job."""
        job_id = f"sync_{user_id}"
        try:
            self._scheduler.remove_job(job_id)
            log.info("scheduler.job_removed", user_id=user_id)
        except Exception:
            log.warning("scheduler.job_not_found", user_id=user_id)

    def _user_scoped_storage(self, user_id: str):
        """Create a storage backend with a user-scoped base path for data isolation."""
        storage_config = self._config.storage
        if storage_config.local is not None:
            user_base = f"{storage_config.local.base_path}/{user_id}"
            user_local = replace(storage_config.local, base_path=user_base)
            storage_config = replace(storage_config, local=user_local)
        return create_storage(storage_config)

    def _sync_user(self, user_id: str) -> None:
        """Run extractors for a single user."""
        log.info("scheduler.sync_start", user_id=user_id)
        try:
            token_provider = make_web_token_provider(
                token_store=self._token_store,
                user_id=user_id,
                auth_config=self._config.auth,
            )
            storage = self._user_scoped_storage(user_id)
            sync_state = SyncState(self._config.state.state_file_path)
            names = list(self._config.extractors.__dataclass_fields__.keys())
            run_extractors(self._config, token_provider, storage, sync_state, names)
            log.info("scheduler.sync_done", user_id=user_id)
        except Exception as exc:
            log.error("scheduler.sync_failed", user_id=user_id, error=str(exc))

    def _compute_interval(self) -> int:
        """Compute the sync interval as the shortest enabled extractor's poll_interval_minutes."""
        intervals = []
        for field_name in self._config.extractors.__dataclass_fields__:
            ext_config = getattr(self._config.extractors, field_name)
            if ext_config.enabled:
                intervals.append(ext_config.poll_interval_minutes)
        if not intervals:
            msg = "No extractors enabled — cannot compute sync interval"
            raise ValueError(msg)
        return min(intervals)
