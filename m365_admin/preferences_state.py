"""Preferences state — per-user extractor toggles and settings."""

from __future__ import annotations

import json

from sqlmodel import select

from m365_admin.auth_state import AuthState
from m365_admin.config_loader import get_session
from m365_extract.models import ExtractorPreference

# All 8 extractors in display order
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


class PreferencesState(AuthState):
    """Manages per-user extractor preferences."""

    # List of dicts: [{extractor_name, enabled, settings_json}, ...]
    preferences: list[dict] = []

    def load_preferences(self) -> None:
        """Load extractor preferences for the current user from DB."""
        if not self.user_id:
            return

        session = get_session()
        try:
            existing = session.exec(
                select(ExtractorPreference).where(ExtractorPreference.user_id == self.user_id)
            ).all()
            existing_map = {p.extractor_name: p for p in existing}

            prefs = []
            for name in EXTRACTOR_NAMES:
                if name in existing_map:
                    p = existing_map[name]
                    prefs.append(
                        {
                            "extractor_name": p.extractor_name,
                            "enabled": p.enabled,
                            "settings_json": p.settings_json,
                        }
                    )
                else:
                    prefs.append(
                        {
                            "extractor_name": name,
                            "enabled": False,
                            "settings_json": "{}",
                        }
                    )
            self.preferences = prefs
        finally:
            session.close()

    def toggle_extractor(self, extractor_name: str) -> None:
        """Toggle an extractor on/off for the current user."""
        if not self.user_id:
            return

        session = get_session()
        try:
            existing = session.exec(
                select(ExtractorPreference).where(
                    ExtractorPreference.user_id == self.user_id,
                    ExtractorPreference.extractor_name == extractor_name,
                )
            ).first()

            if existing is not None:
                existing.enabled = not existing.enabled
                session.add(existing)
            else:
                pref = ExtractorPreference(
                    user_id=self.user_id,
                    extractor_name=extractor_name,
                    enabled=True,
                    settings_json="{}",
                )
                session.add(pref)
            session.commit()
        finally:
            session.close()

        self.load_preferences()

    def update_settings(self, extractor_name: str, settings_dict: dict) -> None:
        """Update per-extractor settings overrides."""
        if not self.user_id:
            return

        session = get_session()
        try:
            existing = session.exec(
                select(ExtractorPreference).where(
                    ExtractorPreference.user_id == self.user_id,
                    ExtractorPreference.extractor_name == extractor_name,
                )
            ).first()

            settings_json = json.dumps(settings_dict)
            if existing is not None:
                existing.settings_json = settings_json
                session.add(existing)
            else:
                pref = ExtractorPreference(
                    user_id=self.user_id,
                    extractor_name=extractor_name,
                    enabled=False,
                    settings_json=settings_json,
                )
                session.add(pref)
            session.commit()
        finally:
            session.close()

        self.load_preferences()
