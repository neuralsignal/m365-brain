"""OAuth callback page — processes the Entra redirect."""

import reflex as rx

from m365_admin.state import AuthState


@rx.page(route="/callback", title="Signing in...", on_load=AuthState.handle_callback)
def callback_page() -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.spinner(size="3"),
            rx.text("Completing sign-in..."),
            spacing="4",
            align="center",
        ),
        height="100vh",
    )
