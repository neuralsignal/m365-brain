"""Tests for the sync worker."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import Session, SQLModel, create_engine, select

from m365_brain.config.errors import ConfigError
from m365_brain.m365.auth.token_provider import TokenRefreshError
from m365_brain.m365.client import GraphApiError
from m365_brain.m365.extractors.errors import ExtractorError
from m365_brain.models import ExtractorPreference, ExtractorStatus, User
from m365_brain.worker import (
    _lock_key,
    _require_worker_config,
    _run_cycle,
    get_due_jobs,
    get_enabled_users,
    get_user_extractors,
    release_advisory_lock,
    run_single_extractor,
    start_worker_thread,
    try_advisory_lock,
    upsert_extractor_status,
    worker_loop,
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(eng)
    return eng


UID_1 = "00000000-0000-4000-8000-000000000001"
UID_2 = "00000000-0000-4000-8000-000000000002"
UID_3 = "00000000-0000-4000-8000-000000000003"


@pytest.fixture()
def seeded_engine(engine):
    """Engine with two users: one enabled, one disabled."""
    with Session(engine) as session:
        session.add(User(user_id=UID_1, display_name="Alice", email="alice@example.com", enabled=True))
        session.add(User(user_id=UID_2, display_name="Bob", email="bob@example.com", enabled=False))
        session.add(User(user_id=UID_3, display_name="Carol", email="carol@example.com", enabled=True))
        session.commit()
    return engine


class TestGetEnabledUsers:
    def test_returns_only_enabled(self, seeded_engine):
        users = get_enabled_users(seeded_engine)
        user_ids = [u.user_id for u in users]
        assert user_ids == [UID_1, UID_3]

    def test_empty_table(self, engine):
        users = get_enabled_users(engine)
        assert users == []


class TestGetUserExtractors:
    def test_returns_enabled(self, seeded_engine):
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="email", enabled=True))
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="calendar", enabled=False))
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="contacts", enabled=True))
            session.commit()

        names = get_user_extractors(seeded_engine, UID_1)
        assert sorted(names) == ["contacts", "email"]

    def test_no_prefs_returns_empty(self, seeded_engine):
        """No ExtractorPreference rows = nothing enabled, no fallback."""
        names = get_user_extractors(seeded_engine, UID_1)
        assert names == []


class TestUpsertExtractorStatus:
    def test_inserts_new(self, seeded_engine):
        upsert_extractor_status(seeded_engine, UID_1, "email", "running", 0, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row is not None
            assert row.status == "running"
            assert row.items_synced == 0

    def test_updates_existing(self, seeded_engine):
        upsert_extractor_status(seeded_engine, UID_1, "email", "running", 0, None)
        upsert_extractor_status(seeded_engine, UID_1, "email", "success", 42, None)

        with Session(seeded_engine) as session:
            rows = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "success"
            assert rows[0].items_synced == 42

    def test_stores_error(self, seeded_engine):
        upsert_extractor_status(seeded_engine, UID_1, "email", "failed", 0, "Token expired")

        with Session(seeded_engine) as session:
            row = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == UID_1)).first()
            assert row.error_message == "Token expired"


class TestGetDueJobs:
    def test_new_user_is_due(self, seeded_engine, full_config):
        """Users with no ExtractorStatus rows are always due."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="email", enabled=True))
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert (UID_1, "email") in pairs

    def test_recently_synced_not_due(self, seeded_engine, full_config):
        """User with a recent sync is not due yet."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, UID_1, "email", "success", 10, None)

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert (UID_1, "email") not in pairs

    def test_old_sync_is_due(self, seeded_engine, full_config):
        """User with an old sync is due again."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, UID_1, "email", "success", 10, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            row.last_run_at = datetime.now(tz=UTC) - timedelta(hours=1)
            session.add(row)
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert (UID_1, "email") in pairs


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
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", return_value=42) as mock_run,
            patch("m365_brain.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        mock_run.assert_called_once()

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "success"
            assert row.items_synced == 42

    def test_failure(self, seeded_engine, full_config, tmp_path):
        from m365_brain.m365.client import GraphApiError

        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", side_effect=GraphApiError("Graph API down", None)),
            patch("m365_brain.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "Graph API down" in row.error_message

    def test_extractor_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", side_effect=ExtractorError("bad extractor")),
            patch("m365_brain.worker.release_advisory_lock") as mock_release,
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "bad extractor" in row.error_message
        mock_release.assert_called_once()

    def test_config_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", side_effect=ConfigError("bad config")),
            patch("m365_brain.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "bad config" in row.error_message

    def test_token_refresh_error(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", side_effect=TokenRefreshError("token expired")),
            patch("m365_brain.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == UID_1,
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "token expired" in row.error_message

    def test_always_releases_lock(self, seeded_engine, full_config, tmp_path):
        """Advisory lock is released even when run_extractors raises."""
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_brain.worker.run_extractors", side_effect=GraphApiError("boom", None)),
            patch("m365_brain.worker.release_advisory_lock") as mock_release,
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        mock_release.assert_called_once_with(seeded_engine, UID_1, "email")

    def test_rejects_non_uuid_user_id(self, engine, full_config, tmp_path):
        with Session(engine) as session:
            bad_user = User(user_id="../traversal", display_name="Evil", email="evil@example.com", enabled=True)
            session.add(bad_user)
            session.commit()
            session.refresh(bad_user)

            with pytest.raises(ConfigError, match="Invalid user_id format"):
                run_single_extractor(full_config, engine, FakeTokenAdapter(), bad_user, "email", str(tmp_path))


class TestLockKey:
    def test_deterministic(self):
        assert _lock_key(UID_1, "email") == _lock_key(UID_1, "email")

    def test_different_inputs_differ(self):
        assert _lock_key(UID_1, "email") != _lock_key(UID_2, "email")
        assert _lock_key(UID_1, "email") != _lock_key(UID_1, "calendar")

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
            session.add(ExtractorPreference(user_id=UID_1, extractor_name="nonexistent", enabled=True))
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert (UID_1, "nonexistent") not in pairs


class TestStartWorkerThread:
    def test_returns_stop_event(self, full_config, engine):
        with patch("m365_brain.worker.get_due_jobs", return_value=[]):
            stop = start_worker_thread(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")
            assert isinstance(stop, threading.Event)
            stop.set()

    def test_stops_on_event(self, full_config, engine):
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            return []

        with patch("m365_brain.worker.get_due_jobs", side_effect=fake_get_due_jobs):
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
        with patch("m365_brain.worker.get_due_jobs", side_effect=KeyboardInterrupt):
            worker_loop(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")

    def test_cycle_unexpected_exception_crashes(self, full_config, engine):
        """worker_loop re-raises unexpected exceptions instead of swallowing them."""
        with (
            patch("m365_brain.worker.get_due_jobs", side_effect=RuntimeError("db gone")),
            patch("m365_brain.worker.time.sleep"),
            pytest.raises(RuntimeError, match="db gone"),
        ):
            worker_loop(full_config, engine, FakeTokenAdapter(), "/tmp/test-worker-state")

    def test_cycle_known_error_resilience(self, full_config, engine):
        """worker_loop survives a known error and continues."""
        call_count = 0

        def fake_get_due_jobs(eng, cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise GraphApiError("rate limited", 429)
            raise KeyboardInterrupt

        with (
            patch("m365_brain.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_brain.worker.time.sleep"),
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
            patch("m365_brain.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_brain.worker.try_advisory_lock", return_value=True),
            patch("m365_brain.worker.run_single_extractor") as mock_run,
            patch("m365_brain.worker.time.sleep"),
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
            patch("m365_brain.worker.get_due_jobs", side_effect=fake_get_due_jobs),
            patch("m365_brain.worker.try_advisory_lock", return_value=False),
            patch("m365_brain.worker.run_single_extractor") as mock_run,
            patch("m365_brain.worker.time.sleep"),
        ):
            worker_loop(full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path))

        mock_run.assert_not_called()


class TestRequireWorkerConfig:
    def test_returns_worker_config(self, full_config):
        result = _require_worker_config(full_config)
        assert result.max_concurrent_jobs == 2
        assert result.poll_interval_seconds == 5

    def test_raises_config_error_when_none(self, full_config):
        config_without_worker = full_config.model_copy(update={"worker": None})
        with pytest.raises(ConfigError, match="'worker' section is required"):
            _require_worker_config(config_without_worker)


class TestTryAdvisoryLock:
    def test_executes_pg_try_advisory_lock_and_returns_bool(self):
        """Executes SELECT pg_try_advisory_lock(:key) with the derived key."""
        with patch("m365_brain.worker.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value.__enter__.return_value
            mock_session.exec.return_value.one.return_value = (True,)

            result = try_advisory_lock("engine-stub", UID_1, "email")

        assert result is True
        mock_session.exec.assert_called_once()
        stmt = mock_session.exec.call_args[0][0]
        assert "pg_try_advisory_lock" in str(stmt)
        assert stmt._bindparams["key"].value == _lock_key(UID_1, "email")

    def test_returns_false_when_lock_unavailable(self):
        """Propagates False when the lock is already held."""
        with patch("m365_brain.worker.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value.__enter__.return_value
            mock_session.exec.return_value.one.return_value = (False,)

            result = try_advisory_lock("engine-stub", UID_1, "email")

        assert result is False


class TestReleaseAdvisoryLock:
    def test_executes_pg_advisory_unlock(self):
        """Executes SELECT pg_advisory_unlock(:key) with the derived key."""
        with patch("m365_brain.worker.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value.__enter__.return_value

            result = release_advisory_lock("engine-stub", UID_1, "email")

        assert result is None
        mock_session.exec.assert_called_once()
        stmt = mock_session.exec.call_args[0][0]
        assert "pg_advisory_unlock" in str(stmt)
        assert stmt._bindparams["key"].value == _lock_key(UID_1, "email")


class TestRunCycleFutureErrors:
    def test_known_error_in_future_logs_job_error(self, seeded_engine, full_config, tmp_path):
        """A _KNOWN_ERRORS raised from the thread pool logs worker.job_error."""
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.get_due_jobs", return_value=[(user, "email")]),
            patch("m365_brain.worker.try_advisory_lock", return_value=True),
            patch("m365_brain.worker.run_single_extractor", side_effect=GraphApiError("boom", None)),
            patch("m365_brain.worker.log") as mock_log,
        ):
            _run_cycle(full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path), 1)

        error_events = [c.args[0] for c in mock_log.error.call_args_list if c.args]
        assert "worker.job_error" in error_events
        critical_events = [c.args[0] for c in mock_log.critical.call_args_list if c.args]
        assert "worker.job_unhandled_error" not in critical_events

    def test_unhandled_error_in_future_logs_critical_and_raises(self, seeded_engine, full_config, tmp_path):
        """An unexpected exception raised from the thread pool logs critical then re-raises."""
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_brain.worker.get_due_jobs", return_value=[(user, "email")]),
            patch("m365_brain.worker.try_advisory_lock", return_value=True),
            patch("m365_brain.worker.run_single_extractor", side_effect=RuntimeError("surprise")),
            patch("m365_brain.worker.log") as mock_log,
            pytest.raises(RuntimeError, match="surprise"),
        ):
            _run_cycle(full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path), 1)

        critical_events = [c.args[0] for c in mock_log.critical.call_args_list if c.args]
        assert "worker.job_unhandled_error" in critical_events


class TestStartWorkerThreadErrorPaths:
    def _run_until_seen(self, seen: threading.Event, stop: threading.Event) -> None:
        assert seen.wait(timeout=5), "mocked cycle was never called"
        stop.set()
        for t in threading.enumerate():
            if t.name == "sync-worker":
                t.join(timeout=5)
                assert not t.is_alive()

    def test_known_error_logs_cycle_failed(self, full_config, engine, tmp_path):
        """start_worker_thread logs worker.cycle_failed when _run_cycle raises _KNOWN_ERRORS."""
        seen = threading.Event()

        def fake_run_cycle(*args, **kwargs):
            seen.set()
            raise GraphApiError("rate limited", 429)

        with (
            patch("m365_brain.worker._run_cycle", side_effect=fake_run_cycle),
            patch("m365_brain.worker.log") as mock_log,
        ):
            stop = start_worker_thread(full_config, engine, FakeTokenAdapter(), str(tmp_path))
            self._run_until_seen(seen, stop)

        error_events = [c.args[0] for c in mock_log.error.call_args_list if c.args]
        assert "worker.cycle_failed" in error_events

    def test_unhandled_error_kills_thread(self, full_config, engine, tmp_path):
        """start_worker_thread's loop terminates when _run_cycle raises unexpected error."""
        seen = threading.Event()

        def fake_run_cycle(*args, **kwargs):
            seen.set()
            raise RuntimeError("db gone")

        with (
            patch("m365_brain.worker._run_cycle", side_effect=fake_run_cycle),
            patch("m365_brain.worker.log") as mock_log,
        ):
            start_worker_thread(full_config, engine, FakeTokenAdapter(), str(tmp_path))
            assert seen.wait(timeout=5), "mocked cycle was never called"
            for t in threading.enumerate():
                if t.name == "sync-worker":
                    t.join(timeout=5)
                    assert not t.is_alive()

        critical_events = [c.args[0] for c in mock_log.critical.call_args_list if c.args]
        assert "worker.cycle_unhandled_error" in critical_events
