"""Tests for the sync scheduler."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from m365_extract.user_manager import UserRecord
from m365_extract.web.scheduler import SyncScheduler


@pytest.fixture()
def mock_token_store():
    return MagicMock()


@pytest.fixture()
def mock_user_manager():
    return MagicMock()


class TestSyncScheduler:
    def test_starts_and_shuts_down(self, full_web_config, mock_token_store, mock_user_manager):
        mock_user_manager.list_users.return_value = []

        scheduler = SyncScheduler(
            config=full_web_config,
            token_store=mock_token_store,
            user_manager=mock_user_manager,
        )
        scheduler.start()
        scheduler.shutdown()

    def test_adds_jobs_for_enabled_users(self, full_web_config, mock_token_store, mock_user_manager):
        mock_user_manager.list_users.return_value = [
            UserRecord(user_id="u1", display_name="A", email="a@x.com", enabled=True, created_at="2026-01-01"),
            UserRecord(user_id="u2", display_name="B", email="b@x.com", enabled=False, created_at="2026-01-01"),
            UserRecord(user_id="u3", display_name="C", email="c@x.com", enabled=True, created_at="2026-01-01"),
        ]

        scheduler = SyncScheduler(
            config=full_web_config,
            token_store=mock_token_store,
            user_manager=mock_user_manager,
        )
        scheduler.start()

        jobs = scheduler._scheduler.get_jobs()
        job_ids = [j.id for j in jobs]
        assert "sync_u1" in job_ids
        assert "sync_u2" not in job_ids
        assert "sync_u3" in job_ids

        scheduler.shutdown()

    def test_removes_job(self, full_web_config, mock_token_store, mock_user_manager):
        mock_user_manager.list_users.return_value = [
            UserRecord(user_id="u1", display_name="A", email="a@x.com", enabled=True, created_at="2026-01-01"),
        ]

        scheduler = SyncScheduler(
            config=full_web_config,
            token_store=mock_token_store,
            user_manager=mock_user_manager,
        )
        scheduler.start()

        assert len(scheduler._scheduler.get_jobs()) == 1
        scheduler.remove_user_job("u1")
        assert len(scheduler._scheduler.get_jobs()) == 0

        scheduler.shutdown()

    def test_sync_interval_uses_shortest_extractor(self, full_web_config, mock_token_store, mock_user_manager):
        # full_web_config has email at 3 min and calendar at 60 min (both enabled)
        scheduler = SyncScheduler(
            config=full_web_config,
            token_store=mock_token_store,
            user_manager=mock_user_manager,
        )
        assert scheduler._interval_minutes == 3

    def test_raises_with_no_extractors(self, full_web_config, mock_token_store, mock_user_manager):
        all_disabled = replace(
            full_web_config.extractors,
            email=replace(full_web_config.extractors.email, enabled=False),
            calendar=replace(full_web_config.extractors.calendar, enabled=False),
            teams_chats=replace(full_web_config.extractors.teams_chats, enabled=False),
            teams_channels=replace(full_web_config.extractors.teams_channels, enabled=False),
            onedrive=replace(full_web_config.extractors.onedrive, enabled=False),
            sharepoint=replace(full_web_config.extractors.sharepoint, enabled=False),
            contacts=replace(full_web_config.extractors.contacts, enabled=False),
            directory=replace(full_web_config.extractors.directory, enabled=False),
        )
        config = replace(full_web_config, extractors=all_disabled)

        with pytest.raises(ValueError, match="No extractors enabled"):
            SyncScheduler(
                config=config,
                token_store=mock_token_store,
                user_manager=mock_user_manager,
            )

    @patch("m365_extract.web.scheduler._run_extractors")
    @patch("m365_extract.web.scheduler.make_web_token_provider")
    @patch("m365_extract.web.scheduler.create_storage")
    @patch("m365_extract.web.scheduler.SyncState")
    def test_sync_user_calls_run_extractors(
        self,
        mock_state_cls,
        mock_storage,
        mock_provider,
        mock_run,
        full_web_config,
        mock_token_store,
        mock_user_manager,
    ):
        mock_user_manager.list_users.return_value = []

        scheduler = SyncScheduler(
            config=full_web_config,
            token_store=mock_token_store,
            user_manager=mock_user_manager,
        )
        scheduler._sync_user("u1")

        mock_provider.assert_called_once()
        mock_run.assert_called_once()
