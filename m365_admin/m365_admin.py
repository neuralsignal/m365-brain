"""App entry point — registers pages, defines the root route, starts worker."""

import os
from pathlib import Path

import reflex as rx

import m365_admin.pages.admin  # noqa: F401
import m365_admin.pages.callback  # noqa: F401
import m365_admin.pages.dashboard  # noqa: F401
import m365_admin.pages.login  # noqa: F401
import m365_admin.pages.settings  # noqa: F401
from m365_admin.auth_state import AuthState


@rx.page(route="/", title="m365 Admin")
def index() -> rx.Component:
    """Root route — redirect to dashboard if authenticated, login otherwise."""
    return rx.cond(
        AuthState.is_authenticated,
        rx.fragment(on_mount=rx.redirect("/dashboard")),
        rx.fragment(on_mount=rx.redirect("/login")),
    )


app = rx.App()

# --- Start background sync worker thread on Reflex app boot ---
# Guard: `reflex export` (Docker build stage) imports this module to discover
# pages but has no env vars, .env, or database. M365_ADMIN_CONFIG is always set
# at runtime (docker-compose, App Service, or .env) so its absence reliably
# indicates a build context. If config is broken at runtime, get_config() crashes
# loud — no silent swallowing.
if os.environ.get("M365_ADMIN_CONFIG"):
    from m365_admin.config_loader import get_config, get_config_path, get_engine
    from m365_admin.services.token_service import TokenService, TokenServiceAdapter
    from m365_extract.worker import start_worker_thread

    _config = get_config()
    _engine = get_engine()

    _token_service = TokenService(fernet_key=_config.web.fernet_key)
    _token_adapter = TokenServiceAdapter(token_service=_token_service, engine=_engine)

    _config_path = get_config_path()
    _first_config = _config_path.split(",")[0].strip()
    _state_dir = str(Path(_first_config).resolve().parent / "state")

    _worker_stop = start_worker_thread(_config, _engine, _token_adapter, _state_dir)
else:
    _worker_stop = None
