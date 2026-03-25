"""Admin state — user management."""

from __future__ import annotations

from sqlmodel import select

from m365_admin.auth_state import AuthState
from m365_admin.config_loader import get_session
from m365_extract.models import SyncRecord, User


class AdminState(AuthState):
    """State for admin page — user list management."""

    users: list[dict] = []

    def load_users(self) -> None:
        """Load all users from DB."""
        session = get_session()
        try:
            rows = session.exec(select(User).order_by(User.user_id)).all()
            self.users = []
            for u in rows:
                latest_sync = session.exec(
                    select(SyncRecord).where(SyncRecord.user_id == u.user_id).order_by(SyncRecord.started_at.desc())  # type: ignore[attr-defined]
                ).first()

                self.users.append(
                    {
                        "user_id": u.user_id,
                        "display_name": u.display_name,
                        "email": u.email,
                        "enabled": u.enabled,
                        "created_at": u.created_at.isoformat() if u.created_at else "",
                        "last_sync": latest_sync.started_at.isoformat() if latest_sync else "Never",
                        "last_sync_status": latest_sync.status if latest_sync else "",
                    }
                )
        finally:
            session.close()

    def toggle_user_enabled(self, user_id: str) -> None:
        """Enable or disable a user."""
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
