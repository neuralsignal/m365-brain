"""Sync state — read-only view of sync history (written by daemon)."""

from __future__ import annotations

import json

from sqlmodel import select

from m365_admin.auth_state import AuthState
from m365_admin.config_loader import get_session
from m365_extract.models import SyncRecord


class SyncState(AuthState):
    """State for viewing sync history. Read-only — daemon writes SyncRecord rows."""

    # Latest sync info
    latest_sync_status: str = ""
    latest_sync_time: str = ""
    latest_sync_items: int = 0
    latest_sync_extractors: str = ""

    # Sync history (list of dicts for rendering)
    sync_history: list[dict] = []

    def load_sync_status(self) -> None:
        """Load the latest sync record and recent history for the current user."""
        if not self.user_id:
            return

        session = get_session()
        try:
            # Latest sync
            latest = session.exec(
                select(SyncRecord)
                .where(SyncRecord.user_id == self.user_id)
                .order_by(SyncRecord.started_at.desc())  # type: ignore[attr-defined]
            ).first()

            if latest is not None:
                self.latest_sync_status = latest.status
                self.latest_sync_time = latest.started_at.isoformat()
                self.latest_sync_items = latest.items_synced
                extractors = json.loads(latest.extractors_run) if latest.extractors_run else []
                self.latest_sync_extractors = ", ".join(extractors) if extractors else "None"
            else:
                self.latest_sync_status = "No syncs yet"
                self.latest_sync_time = ""
                self.latest_sync_items = 0
                self.latest_sync_extractors = ""

            # Recent history (last 20)
            records = session.exec(
                select(SyncRecord)
                .where(SyncRecord.user_id == self.user_id)
                .order_by(SyncRecord.started_at.desc())  # type: ignore[attr-defined]
                .limit(20)
            ).all()

            self.sync_history = []
            for r in records:
                extractors = json.loads(r.extractors_run) if r.extractors_run else []
                self.sync_history.append({
                    "started_at": r.started_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else "",
                    "status": r.status,
                    "extractors": ", ".join(extractors),
                    "items_synced": str(r.items_synced),
                    "error": r.error_message or "",
                })
        finally:
            session.close()
