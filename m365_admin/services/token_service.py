"""Fernet encrypt/decrypt for OAuth tokens.

Operates on TokenRecord via a SQLModel Session (either rx.session() or sqlmodel.Session).
The Fernet key is provided externally from config — never generated here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlmodel import Session, select

from m365_brain.models import TokenRecord

log = structlog.get_logger()


class TokenService:
    """Encrypt, store, retrieve, and delete OAuth tokens."""

    def __init__(self, fernet_key: SecretStr) -> None:
        self._fernet = Fernet(fernet_key.get_secret_value().encode())

    def store_tokens(self, session: Session, user_id: str, tokens: dict) -> None:
        """Encrypt and upsert tokens for a user."""
        plaintext = json.dumps(tokens).encode("utf-8")
        encrypted = self._fernet.encrypt(plaintext)
        now = datetime.now(tz=UTC)

        existing = session.get(TokenRecord, user_id)
        if existing is not None:
            existing.encrypted_tokens = encrypted
            existing.updated_at = now
            session.add(existing)
        else:
            record = TokenRecord(
                user_id=user_id,
                encrypted_tokens=encrypted,
                updated_at=now,
            )
            session.add(record)
        session.commit()
        log.info("token_service.stored", user_id=user_id)

    def get_tokens(self, session: Session, user_id: str) -> dict | None:
        """Retrieve and decrypt tokens. Returns None if not found."""
        record = session.get(TokenRecord, user_id)
        if record is None:
            return None
        decrypted = self._fernet.decrypt(record.encrypted_tokens)
        return json.loads(decrypted)

    def delete_tokens(self, session: Session, user_id: str) -> None:
        """Remove tokens for a user."""
        record = session.get(TokenRecord, user_id)
        if record is not None:
            session.delete(record)
            session.commit()

    def list_user_ids(self, session: Session) -> list[str]:
        """Return all user IDs that have stored tokens."""
        statement = select(TokenRecord.user_id).order_by(TokenRecord.user_id)
        return list(session.exec(statement).all())


class TokenServiceAdapter:
    """Adapts TokenService + engine into TokenStoreProtocol for the daemon.

    Each call creates a short-lived session — correct for long-lived daemon processes
    where holding a single session open would accumulate stale state.
    """

    def __init__(self, token_service: TokenService, engine) -> None:
        self._svc = token_service
        self._engine = engine

    def get_tokens(self, user_id: str) -> dict | None:
        with Session(self._engine) as session:
            return self._svc.get_tokens(session, user_id)

    def store_tokens(self, user_id: str, tokens: dict) -> None:
        with Session(self._engine) as session:
            self._svc.store_tokens(session, user_id, tokens)
