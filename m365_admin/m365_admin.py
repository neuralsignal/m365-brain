"""App entry point — registers pages and defines the root route."""

import reflex as rx

import m365_admin.pages.callback  # noqa: F401
import m365_admin.pages.dashboard  # noqa: F401
import m365_admin.pages.login  # noqa: F401
from m365_admin.state import AuthState


@rx.page(route="/", title="m365 Admin")
def index() -> rx.Component:
    """Root route — redirect to dashboard if authenticated, login otherwise."""
    return rx.cond(
        AuthState.is_authenticated,
        rx.fragment(on_mount=rx.redirect("/dashboard")),
        rx.fragment(on_mount=rx.redirect("/login")),
    )


app = rx.App()
