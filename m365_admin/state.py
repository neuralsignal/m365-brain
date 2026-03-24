"""Auth state — Entra OAuth2 flow via Reflex state management.

Gotchas:
    - Reflex fires on_load multiple times per page load (SSR + hydration).
      All on_load handlers must be idempotent. handle_callback() early-returns
      if user_id is already set.
    - External OAuth redirects (Entra) disconnect the WebSocket and reset
      Reflex state. OAuth state tokens are persisted to state/oauth_state.json
      instead of using backend vars.
    - State tokens are NOT consumed on verify — they expire via 10-minute TTL
      and are pruned lazily on the next _store_oauth_state() call.
    - handle_callback() offloads MSAL + SQLite calls to asyncio.to_thread()
      to avoid blocking the Granian event loop. Without this, dev hot reloads
      caused 'Killing worker-0' warnings.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

import reflex as rx

from m365_admin.config_loader import get_config
from m365_extract.auth.auth_code import AuthCodeAuth, AuthCodeError
from m365_extract.auth.token_store import TokenStore
from m365_extract.user_manager import UserManager

# OAuth state tokens expire after 10 minutes
_OAUTH_STATE_TTL_SECONDS = 600


def extract_user_info(token_response: dict) -> dict:
    """Extract user_id, display_name, and email from an MSAL token response.

    Pure function — suitable for property-based testing.
    Returns dict with keys: user_id, display_name, email.
    Raises KeyError if id_token_claims is missing required fields.
    """
    claims = token_response["id_token_claims"]
    return {
        "user_id": claims["oid"],
        "display_name": claims.get("name", ""),
        "email": claims.get("preferred_username", ""),
    }


def _oauth_state_path() -> Path:
    """Return path to the file-based OAuth state store."""
    config = get_config()
    # db_path is in the state/ directory — use its parent
    return Path(config.web.db_path).parent / "oauth_state.json"


def _store_oauth_state(state_token: str) -> None:
    """Persist an OAuth state token to disk so it survives Reflex state loss."""
    path = _oauth_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, float] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))

    # Prune expired tokens
    now = time.time()
    data = {k: v for k, v in data.items() if now - v < _OAUTH_STATE_TTL_SECONDS}

    data[state_token] = now
    path.write_text(json.dumps(data), encoding="utf-8")


def _verify_oauth_state(state_token: str) -> bool:
    """Check if a state token exists and is not expired.

    Does NOT consume the token — Reflex fires on_load multiple times per page
    load, so the token must remain valid for subsequent calls. Expired tokens
    are pruned on the next _store_oauth_state() call.
    """
    path = _oauth_state_path()
    if not path.exists():
        return False

    data: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))
    timestamp = data.get(state_token)
    if timestamp is None:
        return False

    if time.time() - timestamp > _OAUTH_STATE_TTL_SECONDS:
        # Expired — remove and reject
        del data[state_token]
        path.write_text(json.dumps(data), encoding="utf-8")
        return False

    return True


def _make_services() -> tuple[UserManager, TokenStore, AuthCodeAuth]:
    """Create fresh UserManager, TokenStore, and AuthCodeAuth from config.

    Creates new instances each call to avoid storing unpicklable sqlite3.Connection
    objects in Reflex state (which must be serializable between requests).
    """
    config = get_config()
    web = config.web
    if web is None:
        msg = (
            "config.web is None — the web section in config.web.yaml failed to load. "
            "Ensure SECRET_KEY and FERNET_KEY are set in .env at the repo root."
        )
        raise RuntimeError(msg)
    user_manager = UserManager(db_path=web.db_path, check_same_thread=False)
    token_store = TokenStore(
        db_path=web.db_path,
        fernet_key=web.fernet_key,
        check_same_thread=False,
    )
    auth = AuthCodeAuth(auth_config=config.auth)
    return user_manager, token_store, auth


def _get_redirect_uri() -> str:
    """Read redirect URI from env. Crashes if unset."""
    uri = os.environ.get("M365_ADMIN_REDIRECT_URI")
    if uri is None:
        msg = "M365_ADMIN_REDIRECT_URI environment variable is not set"
        raise RuntimeError(msg)
    return uri


class AuthState(rx.State):
    """Manages Entra OAuth2 auth code flow."""

    # Client-visible vars
    user_display_name: str = ""
    user_email: str = ""
    user_id: str = ""
    auth_error: str = ""

    # Backend-only vars (not sent to client) — only picklable types
    _access_token: str = ""

    @rx.var
    def is_authenticated(self) -> bool:
        """True when a user has completed the OAuth flow."""
        return self.user_id != ""

    def login(self) -> rx.event.EventSpec:
        """Start OAuth2 auth code flow — redirect to Entra login page."""
        _user_manager, _token_store, auth = _make_services()
        self.auth_error = ""
        state_token = secrets.token_urlsafe(32)
        _store_oauth_state(state_token)
        redirect_uri = _get_redirect_uri()
        auth_url = auth.get_auth_url(redirect_uri=redirect_uri, state=state_token)
        return rx.redirect(auth_url, is_external=True)

    async def handle_callback(self) -> rx.event.EventSpec | None:
        """Process the OAuth callback — exchange code for tokens, create/fetch user.

        Blocking MSAL and SQLite calls are offloaded to a thread so the Granian
        event loop stays responsive (avoids 'Killing worker-0' during dev hot reload).
        """
        # Reflex fires on_load multiple times per page load. If the first call
        # already completed the auth flow, skip directly to dashboard.
        if self.user_id != "":
            return rx.redirect("/dashboard")

        params = dict(self.router.url.query_parameters)

        # Check for error from Entra
        error = params.get("error", "")
        if error:
            error_desc = params.get("error_description", error)
            self.auth_error = error_desc
            return rx.redirect("/login")

        code = params.get("code", "")
        returned_state = params.get("state", "")

        if not code:
            self.auth_error = "No authorization code received"
            return rx.redirect("/login")

        # CSRF check — state persisted to disk to survive Reflex state loss
        # during external redirect to Entra
        if not _verify_oauth_state(returned_state):
            self.auth_error = "Invalid state parameter — possible CSRF attack"
            return rx.redirect("/login")

        user_manager, token_store, auth = _make_services()
        redirect_uri = _get_redirect_uri()

        # Offload blocking MSAL HTTP call to a thread
        try:
            token_response = await asyncio.to_thread(
                auth.acquire_token_by_code, code=code, redirect_uri=redirect_uri
            )
        except AuthCodeError as exc:
            self.auth_error = str(exc)
            return rx.redirect("/login")

        # Extract user info from token claims (pure, no I/O)
        try:
            user_info = extract_user_info(token_response)
        except (KeyError, TypeError):
            self.auth_error = "Could not extract user info from token response"
            return rx.redirect("/login")

        # Offload blocking SQLite calls to a thread
        await asyncio.to_thread(
            token_store.store_tokens, user_id=user_info["user_id"], tokens=token_response
        )

        existing = await asyncio.to_thread(user_manager.get_user, user_info["user_id"])
        if existing is None:
            await asyncio.to_thread(
                user_manager.create_user,
                user_id=user_info["user_id"],
                display_name=user_info["display_name"],
                email=user_info["email"],
            )

        # Set client-visible state
        self.user_id = user_info["user_id"]
        self.user_display_name = user_info["display_name"]
        self.user_email = user_info["email"]
        self._access_token = token_response.get("access_token", "")

        return rx.redirect("/dashboard")

    def logout(self) -> rx.event.EventSpec:
        """Clear auth state and redirect to login."""
        self.user_id = ""
        self.user_display_name = ""
        self.user_email = ""
        self.auth_error = ""
        self._access_token = ""
        return rx.redirect("/login")

    def check_auth(self) -> rx.event.EventSpec | None:
        """Redirect to login if not authenticated. Use as on_load for protected pages."""
        if not self.is_authenticated:
            return rx.redirect("/login")
        return None
