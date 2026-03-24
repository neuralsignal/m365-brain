"""Login page — "Sign in with Microsoft" entry point."""

import reflex as rx

from m365_admin.state import AuthState


@rx.page(route="/login", title="Sign In — m365 Admin")
def login_page() -> rx.Component:
    return rx.center(
        rx.card(
            rx.vstack(
                rx.heading("m365 Admin", size="6"),
                rx.text("Sign in with your Microsoft account to manage sync settings."),
                rx.cond(
                    AuthState.auth_error != "",
                    rx.callout(
                        AuthState.auth_error,
                        icon="triangle_alert",
                        color_scheme="red",
                        width="100%",
                    ),
                ),
                rx.button(
                    "Sign in with Microsoft",
                    on_click=AuthState.login,
                    size="3",
                    width="100%",
                ),
                spacing="4",
                align="center",
                width="100%",
            ),
            width="400px",
        ),
        height="100vh",
    )
