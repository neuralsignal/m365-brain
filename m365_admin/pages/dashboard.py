"""Dashboard page — post-login user profile view."""

import reflex as rx

from m365_admin.state import AuthState


@rx.page(route="/dashboard", title="Dashboard — m365 Admin", on_load=AuthState.check_auth)
def dashboard_page() -> rx.Component:
    return rx.box(
        rx.flex(
            rx.heading("m365 Admin", size="5"),
            rx.spacer(),
            rx.button("Sign out", on_click=AuthState.logout, variant="outline", size="2"),
            width="100%",
            align="center",
            padding="4",
            border_bottom="1px solid var(--gray-5)",
        ),
        rx.center(
            rx.card(
                rx.vstack(
                    rx.heading("Your Profile", size="4"),
                    rx.separator(),
                    _profile_row("Name", AuthState.user_display_name),
                    _profile_row("Email", AuthState.user_email),
                    _profile_row("User ID", AuthState.user_id),
                    spacing="3",
                    width="100%",
                ),
                width="500px",
            ),
            padding_top="8",
        ),
        width="100%",
    )


def _profile_row(label: str, value: rx.Var) -> rx.Component:
    return rx.flex(
        rx.text(label, weight="bold", width="100px"),
        rx.text(value),
        spacing="2",
        align="center",
    )
