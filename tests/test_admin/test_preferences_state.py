"""Tests for extractor preference logic (DB operations, not Reflex state)."""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.models import ExtractorPreference, User

pytestmark = pytest.mark.admin

EXTRACTOR_NAMES = [
    "email",
    "calendar",
    "teams_chats",
    "teams_channels",
    "onedrive",
    "sharepoint",
    "contacts",
    "directory",
]


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        s.commit()
        yield s


class TestExtractorPreferences:
    def test_create_preference(self, session):
        pref = ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True)
        session.add(pref)
        session.commit()

        result = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        assert result is not None
        assert result.enabled is True
        assert result.settings_json == "{}"

    def test_toggle_preference(self, session):
        pref = ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True)
        session.add(pref)
        session.commit()

        # Toggle off
        record = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        record.enabled = not record.enabled
        session.add(record)
        session.commit()

        refreshed = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        assert refreshed.enabled is False

    def test_update_settings(self, session):
        pref = ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True)
        session.add(pref)
        session.commit()

        record = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        settings = {"folders": ["Inbox", "SentItems"], "lookback_days": 30}
        record.settings_json = json.dumps(settings)
        session.add(record)
        session.commit()

        refreshed = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        assert json.loads(refreshed.settings_json) == settings

    def test_all_extractors_for_user(self, session):
        """Create preferences for all 8 extractors."""
        for name in EXTRACTOR_NAMES:
            session.add(ExtractorPreference(user_id="u-1", extractor_name=name, enabled=True))
        session.commit()

        results = session.exec(select(ExtractorPreference).where(ExtractorPreference.user_id == "u-1")).all()
        assert len(results) == 8
        names = {r.extractor_name for r in results}
        assert names == set(EXTRACTOR_NAMES)

    def test_preferences_isolated_per_user(self, session):
        """Different users have independent preferences."""
        session.add(User(user_id="u-2", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
        session.add(ExtractorPreference(user_id="u-2", extractor_name="email", enabled=False))
        session.commit()

        u1_pref = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()
        u2_pref = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-2",
                ExtractorPreference.extractor_name == "email",
            )
        ).first()

        assert u1_pref.enabled is True
        assert u2_pref.enabled is False

    def test_default_settings_json_is_empty(self, session):
        pref = ExtractorPreference(user_id="u-1", extractor_name="calendar", enabled=True)
        session.add(pref)
        session.commit()

        result = session.exec(
            select(ExtractorPreference).where(
                ExtractorPreference.user_id == "u-1",
                ExtractorPreference.extractor_name == "calendar",
            )
        ).first()
        assert json.loads(result.settings_json) == {}
