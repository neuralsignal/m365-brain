"""User management for multi-user sync. CRUD operations backed by SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class UserRecord:
    """Immutable representation of a managed user."""

    user_id: str
    display_name: str
    email: str
    enabled: bool
    created_at: str


class UserManager:
    """SQLite-backed user manager for multi-user sync scheduling."""

    def __init__(self, db_path: str, check_same_thread: bool) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                email TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                extractor_preferences TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
            """
        )
        self._conn.commit()

    def create_user(self, user_id: str, display_name: str, email: str) -> UserRecord:
        """Create a new user. Raises ValueError if user_id already exists."""
        try:
            self._conn.execute(
                "INSERT INTO users (user_id, display_name, email) VALUES (?, ?, ?)",
                (user_id, display_name, email),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as err:
            msg = f"user '{user_id}' already exists"
            raise ValueError(msg) from err
        log.info("user_manager.created", user_id=user_id)
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> UserRecord | None:
        """Retrieve a user by ID. Returns None if not found."""
        row = self._conn.execute(
            "SELECT user_id, display_name, email, enabled, created_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return UserRecord(
            user_id=row[0],
            display_name=row[1],
            email=row[2],
            enabled=bool(row[3]),
            created_at=row[4],
        )

    def list_users(self) -> list[UserRecord]:
        """Return all users ordered by user_id."""
        rows = self._conn.execute(
            "SELECT user_id, display_name, email, enabled, created_at FROM users ORDER BY user_id"
        ).fetchall()
        return [
            UserRecord(
                user_id=row[0],
                display_name=row[1],
                email=row[2],
                enabled=bool(row[3]),
                created_at=row[4],
            )
            for row in rows
        ]

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        """Enable or disable a user. Raises ValueError if user not found."""
        cursor = self._conn.execute(
            "UPDATE users SET enabled = ? WHERE user_id = ?",
            (int(enabled), user_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            msg = f"user '{user_id}' not found"
            raise ValueError(msg)

    def delete_user(self, user_id: str) -> None:
        """Remove a user."""
        self._conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def set_extractor_preferences(self, user_id: str, preferences: dict) -> None:
        """Set extractor on/off preferences for a user. Raises ValueError if not found."""
        cursor = self._conn.execute(
            "UPDATE users SET extractor_preferences = ? WHERE user_id = ?",
            (json.dumps(preferences), user_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            msg = f"user '{user_id}' not found"
            raise ValueError(msg)

    def get_extractor_preferences(self, user_id: str) -> dict:
        """Get extractor preferences for a user. Returns empty dict if none set."""
        row = self._conn.execute(
            "SELECT extractor_preferences FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            msg = f"user '{user_id}' not found"
            raise ValueError(msg)
        return json.loads(row[0])
