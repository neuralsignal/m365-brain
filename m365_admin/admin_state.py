"""Admin state — user management."""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import select

from m365_admin.auth_state import AuthState
from m365_admin.config_loader import get_session
from m365_brain.models import ExtractorStatus, User


class AdminState(AuthState):
    """State for admin page — user list management."""

    users: list[dict] = []

    def load_users(self) -> None:
        """Load all users with latest sync status in a single query."""
        if not self.is_admin:
            return
        session = get_session()
        try:
            latest_run = (
                select(
                    ExtractorStatus.user_id,
                    func.max(ExtractorStatus.last_run_at).label("max_run_at"),
                )
                .group_by(ExtractorStatus.user_id)
                .subquery()
            )

            stmt = (
                select(
                    User,
                    ExtractorStatus.last_run_at,
                    ExtractorStatus.status,
                )
                .outerjoin(
                    latest_run,
                    User.user_id == latest_run.c.user_id,
                )
                .outerjoin(
                    ExtractorStatus,
                    (ExtractorStatus.user_id == latest_run.c.user_id)
                    & (ExtractorStatus.last_run_at == latest_run.c.max_run_at),
                )
                .order_by(User.user_id)
            )

            rows = session.exec(stmt).all()
            self.users = [
                {
                    "user_id": u.user_id,
                    "display_name": u.display_name,
                    "email": u.email,
                    "enabled": u.enabled,
                    "created_at": u.created_at.isoformat() if u.created_at else "",
                    "last_sync": last_run_at.isoformat() if last_run_at else "Never",
                    "last_sync_status": status or "",
                }
                for u, last_run_at, status in rows
            ]
        finally:
            session.close()

    def toggle_user_enabled(self, user_id: str) -> None:
        """Enable or disable a user."""
        if not self.is_admin:
            return
        session = get_session()
        try:
            user = session.get(User, user_id)
            if user is not None:
                user.enabled = not user.enabled
                session.add(user)
                session.commit()
        finally:
            session.close()
        self.load_users()
