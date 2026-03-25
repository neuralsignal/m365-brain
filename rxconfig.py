"""Reflex framework configuration."""

import os
from pathlib import Path

import reflex as rx
from dotenv import load_dotenv
from reflex.plugins import SitemapPlugin

# Load .env before Reflex reads DATABASE_URL — rxconfig is imported
# before any application code, so config_loader hasn't run yet.
load_dotenv(Path(__file__).parent / ".env", override=False)

# DATABASE_URL is required in production. Falls back to SQLite for test
# environments where .env is not present and the env var is unset.
_db_url = os.environ.get("DATABASE_URL", "sqlite:///state/web.db")

config = rx.Config(
    app_name="m365_admin",
    db_url=_db_url,
    disable_plugins=[SitemapPlugin],
)
