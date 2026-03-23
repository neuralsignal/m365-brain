"""OAuth2 auth code flow endpoints for user authentication."""

from __future__ import annotations

import secrets

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from m365_extract.auth.auth_code import AuthCodeAuth, AuthCodeError
from m365_extract.auth.token_store import TokenStore
from m365_extract.config import Config
from m365_extract.user_manager import UserManager
from m365_extract.web.dependencies import get_config, get_token_store, get_user_manager

log = structlog.get_logger()

router = APIRouter(prefix="/auth")


@router.get("/login")
def login(request: Request, config: Config = Depends(get_config)) -> RedirectResponse:
    """Redirect to Entra authorization page."""
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    auth = AuthCodeAuth(config.auth)
    redirect_uri = str(request.url_for("callback"))
    url = auth.get_auth_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url=url)


@router.get("/callback")
def callback(
    request: Request,
    code: str,
    state: str,
    config: Config = Depends(get_config),
    token_store: TokenStore = Depends(get_token_store),
    user_manager: UserManager = Depends(get_user_manager),
) -> JSONResponse:
    """Handle OAuth2 callback: exchange code for tokens, create/update user."""
    expected_state = request.session.get("oauth_state")
    if state != expected_state:
        log.warning("auth.callback_bad_state", expected=expected_state, got=state)
        return JSONResponse({"error": "Invalid OAuth state"}, status_code=400)

    auth = AuthCodeAuth(config.auth)
    redirect_uri = str(request.url_for("callback"))

    try:
        result = auth.acquire_token_by_code(code=code, redirect_uri=redirect_uri)
    except AuthCodeError as exc:
        log.error("auth.callback_token_error", error=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=400)

    claims = result.get("id_token_claims", {})
    user_id = claims.get("oid", claims.get("sub", ""))
    display_name = claims.get("name", "")
    email = claims.get("preferred_username", "")

    tokens_to_store = {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "expires_in": result.get("expires_in", 3600),
    }

    token_store.store_tokens(user_id, tokens_to_store)

    existing = user_manager.get_user(user_id)
    if existing is None:
        user_manager.create_user(user_id=user_id, display_name=display_name, email=email)
        log.info("auth.user_created", user_id=user_id)

    request.session["user_id"] = user_id
    request.session.pop("oauth_state", None)

    return JSONResponse({"status": "authenticated", "user_id": user_id})


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    """Clear the user session."""
    request.session.clear()
    return JSONResponse({"status": "logged_out"})
