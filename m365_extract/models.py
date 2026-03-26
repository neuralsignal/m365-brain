"""SQLModel table definitions shared between Reflex admin UI and sync daemon.

Plain SQLModel (not rx.Model) so both rx.session() and sqlmodel.Session can use them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class User(SQLModel, table=True):
    """A managed user (Entra OID as primary key)."""

    user_id: str = Field(primary_key=True)
    display_name: str
    email: str
    enabled: bool
    created_at: datetime = Field(default_factory=_utcnow)


class TokenRecord(SQLModel, table=True):
    """Fernet-encrypted OAuth tokens per user."""

    user_id: str = Field(primary_key=True, foreign_key="user.user_id")
    encrypted_tokens: bytes
    updated_at: datetime = Field(default_factory=_utcnow)


class ExtractorPreference(SQLModel, table=True):
    """Per-user extractor on/off toggle + settings overrides."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="user.user_id")
    extractor_name: str
    enabled: bool
    settings_json: str = Field(default="{}")


class ExtractorStatus(SQLModel, table=True):
    """Per-(user, extractor) sync status. Single row per pair, upserted after each run."""

    __table_args__ = (UniqueConstraint("user_id", "extractor_name"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(foreign_key="user.user_id")
    extractor_name: str
    status: str  # idle, running, success, failed
    last_run_at: datetime | None = None
    items_synced: int = Field(default=0)
    error_message: str | None = None
