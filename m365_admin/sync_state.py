"""Sync state — read-only view of per-extractor sync status (written by worker)."""

from __future__ import annotations

from sqlmodel import select

from m365_admin.auth_state import AuthState
from m365_admin.config_loader import get_session
from m365_brain.models import ExtractorStatus


class SyncState(AuthState):
    """State for viewing extractor sync status. Read-only — worker writes ExtractorStatus rows."""

    extractor_statuses: list[dict] = []

    def load_sync_status(self) -> None:
        """Load per-extractor status for the current user."""
        if not self.user_id:
            return

        session = get_session()
        try:
            rows = session.exec(
                select(ExtractorStatus)
                .where(ExtractorStatus.user_id == self.user_id)
                .order_by(ExtractorStatus.extractor_name)
            ).all()

            self.extractor_statuses = [
                {
                    "name": r.extractor_name,
                    "status": r.status,
                    "last_run_at": r.last_run_at.isoformat() if r.last_run_at else "",
                    "items_synced": str(r.items_synced),
                    "error": r.error_message or "",
                }
                for r in rows
            ]
        finally:
            session.close()
