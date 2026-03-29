"""Tests for the sync worker."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.auth.token_provider import TokenRefreshError
from m365_extract.config.errors import ConfigError
from m365_extract.extractors.errors import ExtractorError
from m365_extract.graph_client import GraphApiError
from m365_extract.models import ExtractorPreference, ExtractorStatus, User
from m365_extract.worker import (
    _lock_key,
    get_due_jobs,
    get_enabled_users,
    get_user_extractors,
    run_single_extractor,
    start_worker_thread,
    upsert_extractor_status,
    worker_loop,
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def seeded_engine(engine):
    """Engine with two users: one enabled, one disabled."""
    with Session(engine) as session:
        session.add(User(user_id="u-1", display_name="Alice", email="alice@example.com", enabled=True))
        session.add(User(user_id="u-2", display_name="Bob", email="bob@example.com", enabled=False))
        session.add(User(user_id="u-3", display_name="Carol", email="carol@example.com", enabled=True))
        session.commit()
    return engine


class TestGetEnabledUsers:
    def test_returns_only_enabled(self, seeded_engine):
        users = get_enabled_users(seeded_engine)
        user_ids = [u.user_id for u in users]
        assert user_ids == ["u-1", "u-3"]

    def test_empty_table(self, engine):
        users = get_enabled_users(engine)
        assert users == []


class TestGetUserExtractors:
    def test_returns_enabled(self, seeded_engine):
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.add(ExtractorPreference(user_id="u-1", extractor_name="calendar", enabled=False))
            session.add(ExtractorPreference(user_id="u-1", extractor_name="contacts", enabled=True))
            session.commit()

        names = get_user_extractors(seeded_engine, "u-1")
        assert sorted(names) == ["contacts", "email"]

    def test_no_prefs_returns_empty(self, seeded_engine):
        """No ExtractorPreference rows = nothing enabled, no fallback."""
        names = get_user_extractors(seeded_engine, "u-1")
        assert names == []


class TestUpsertExtractorStatus:
    def test_inserts_new(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "running", 0, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row is not None
            assert row.status == "running"
            assert row.items_synced == 0

    def test_updates_existing(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "running", 0, None)
        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 42, None)

        with Session(seeded_engine) as session:
            rows = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "success"
            assert rows[0].items_synced == 42

    def test_stores_error(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "failed", 0, "Token expired")

        with Session(seeded_engine) as session:
            row = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).first()
            assert row.error_message == "Token expired"


class TestGetDueJobs:
    def test_new_user_is_due(self, seeded_engine, full_config):
        """Users with no ExtractorStatus rows are always due."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") in pairs

    def test_recently_synced_not_due(self, seeded_engine, full_config):
        """User with a recent sync is not due yet."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 10, None)

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") not in pairs

    def test_old_sync_is_due(self, seeded_engine, full_config):
        """User with an old sync is due again."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 10, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            row.last_run_at = datetime.now(tz=UTC) - timedelta(hours=1)
            session.add(row)
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") in pairs


def _fake_token_provider():
    return "fake-access-token"


class FakeTokenAdapter:
    def get_tokens(self, user_id):
        return {"access_token": "fake", "refresh_token": "fake", "expires_at": 9999999999}

    def store_tokens(self, user_id, tokens):
        pass


class TestRunSingleExtractor:
    def test_success(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", return_value=42) as mock_run,
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        mock_run.assert_called_once()

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "success"
            assert row.items_synced == 42

    def test_failure(self, seeded_engine, full_config, tmp_path):
        from m365_extract.graph_client import GraphApiError

        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=GraphApiError("Graph API down")),
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "Graph API down" in row.error_message

    def test_extractor_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=ExtractorError("bad extractor")),
            patch("m365_extract.worker.release_advisory_lock") as mock_release,
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "bad extractor" in row.error_message
        mock_release.assert_called_once()

    def test_config_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=ConfigError("bad config")),
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "bad config" in row.error_message

    def test_token_refresh_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=TokenRefreshError("token expired")),
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "token expired" in row.error_message

    def test_always_releases_lock(self, seeded_engine, full_config, tmp_path):
        """Advisory lock is released even when run_extractors raises."""
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=GraphApiError("boom")),
            patch("m365_extract.worker.release_advisory_lock") as mock_release,
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        mock_release.assert_called_once_with(seeded_engine, "u-1", "email")


class TestLockKey:
    def test_deterministic(self):
        assert _lock_key("u-1", "email") == _lock_key("u-1", "email")

    def test_different_inputs_differ(self):
        assert _lock_key("u-1", "email") != _lock_key("u-2", "email")
        assert _lock_key("u-1", "email") != _lock_key("u-1", "calendar")

    @given(
        user_id=st.text(min_size=1, max_size=100),
        extractor=st.text(min_size=1, max_size=100),
    )
    def test_is_signed_int64(self, user_id, extractor):
        key = _lock_key(user_id, extractor)
        assert -(2**63) <= key <= 2**63 - 1


class TestGetDueJobsEdgeCases:
    def test_skips_unknown_extractor(self, seeded_engine, full_config):
        """Extractor name not in EXTRACTORS dict is skipped."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="nonexistent", enabled=True))
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "nonexistent") not in pairs


class TestStartWorkerThread:
    def test_returns_stop_event(self, full_config, engine):
        with patch("m365_extract.worker.get_due_jobs", return_value=[]):
            stop = start_worker_thread(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")
            assert isinstance(stop, threading.Event)
            stop.set()

    def test_stops_on_event(self, full_config, engine):
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            return []

        with patch("m365_extract.worker.get_due_jobs", side_effect=fake_get_due_jobs):
            stop = start_worker_thread(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")
            # Let the loop run at least once
            import time

            time.sleep(0.1)
            stop.set()
            # Wait for thread to exit
            for t in threading.enumerate():
                if t.name == "sync-worker":
                    t.join(timeout=2)
                    assert not t.is_alive()
        assert call_count >= 1


class TestWorkerLoop:
    def test_keyboard_interrupt_exits(self, full_config, engine):
        """worker_loop exits cleanly on KeyboardInterrupt."""
        with patch("m365_extract.worker.get_due_jobs", side_effect=KeyboardInterrupt):
            worker_loop(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")

    def test_cycle_exception_resilience(self, full_config, engine):
        """worker_loop survives one cycle exception and continues."""
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("db gone")
            raise KeyboardInterrupt

        with (
            patch("m365_extract.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_extract.worker.time.sleep"),
        ):
            worker_loop(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")
        assert call_count == 2

    def test_submits_due_jobs(self, seeded_engine, full_config, tmp_path):
        """worker_loop acquires lock and submits due jobs."""
        user = get_enabled_users(seeded_engine)[0]
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [(user, "email")]
            raise KeyboardInterrupt

        with (
            patch("m365_extract.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_extract.worker.try_advisory_lock", return_value=True),
            patch("m365_extract.worker.run_single_extractor") as mock_run,
            patch("m365_extract.worker.time.sleep"),
        ):
            worker_loop(full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path))

        mock_run.assert_called_once()

    def test_skips_locked_jobs(self, seeded_engine, full_config, tmp_path):
        """worker_loop skips jobs that are already locked."""
        user = get_enabled_users(seeded_engine)[0]
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [(user, "email")]
            raise KeyboardInterrupt

        with (
            patch("m365_extract.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_extract.worker.try_advisory_lock", return_value=False),
            patch("m365_extract.worker.run_single_extractor") as mock_run,
            patch("m365_extract.worker.time.sleep"),
        ):
            worker_loop(full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path))

        mock_run.assert_not_called()
