"""Reflex framework configuration."""

import reflex as rx
from reflex.plugins import SitemapPlugin

config = rx.Config(
    app_name="m365_admin",
    disable_plugins=[SitemapPlugin],
)
