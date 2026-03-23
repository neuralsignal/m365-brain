"""Encrypted token storage using SQLite + Fernet symmetric encryption.

Tokens are stored encrypted at rest. The Fernet key must be provided
externally (from config) — never generated or stored by this module.
"""

from __future__ import annotations

import json
import sqlite3

import structlog
from cryptography.fernet import Fernet

log = structlog.get_logger()


class TokenStore:
    """SQLite-backed encrypted token store for multi-user OAuth tokens."""

    def __init__(self, db_path: str, fernet_key: str, check_same_thread: bool) -> None:
        self._fernet = Fernet(fernet_key.encode())
        self._conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                user_id TEXT PRIMARY KEY,
                encrypted_tokens BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
            """
        )
        self._conn.commit()

    def store_tokens(self, user_id: str, tokens: dict) -> None:
        """Store or update encrypted tokens for a user."""
        plaintext = json.dumps(tokens).encode("utf-8")
        encrypted = self._fernet.encrypt(plaintext)
        self._conn.execute(
            """
            INSERT INTO tokens (user_id, encrypted_tokens, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_tokens = excluded.encrypted_tokens,
                updated_at = excluded.updated_at
            """,
            (user_id, encrypted),
        )
        self._conn.commit()
        log.info("token_store.stored", user_id=user_id)

    def get_tokens(self, user_id: str) -> dict | None:
        """Retrieve and decrypt tokens for a user. Returns None if not found."""
        row = self._conn.execute(
            "SELECT encrypted_tokens FROM tokens WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        decrypted = self._fernet.decrypt(row[0])
        return json.loads(decrypted)

    def delete_tokens(self, user_id: str) -> None:
        """Remove tokens for a user."""
        self._conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def list_users(self) -> list[str]:
        """Return all user IDs that have stored tokens."""
        rows = self._conn.execute("SELECT user_id FROM tokens ORDER BY user_id").fetchall()
        return [row[0] for row in rows]
