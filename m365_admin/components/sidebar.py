"""Navigation sidebar component."""

import reflex as rx

from m365_admin.auth_state import AuthState


def _nav_link(label: str, href: str, icon: str) -> rx.Component:
    """A single navigation link."""
    return rx.link(
        rx.flex(
            rx.icon(icon, size=16),
            rx.text(label, size="2"),
            spacing="2",
            align="center",
            padding="2",
            border_radius="6px",
            _hover={"background": "var(--gray-3)"},
        ),
        href=href,
        underline="none",
        width="100%",
    )


def sidebar() -> rx.Component:
    """Navigation sidebar with links to all pages."""
    return rx.box(
        rx.vstack(
            rx.heading("m365 Admin", size="4", padding="4"),
            rx.separator(),
            rx.vstack(
                _nav_link("Dashboard", "/dashboard", "layout_dashboard"),
                _nav_link("Settings", "/settings", "settings"),
                rx.cond(
                    AuthState.is_admin,
                    _nav_link("Admin", "/admin", "shield"),
                    rx.fragment(),
                ),
                spacing="1",
                padding_x="3",
                padding_y="2",
                width="100%",
            ),
            rx.spacer(),
            rx.box(
                rx.vstack(
                    rx.separator(),
                    rx.flex(
                        rx.text(AuthState.user_display_name, size="1", weight="bold"),
                        rx.text(AuthState.user_email, size="1", color="gray"),
                        direction="column",
                        padding_x="4",
                        padding_y="2",
                    ),
                    rx.box(
                        rx.button(
                            "Sign out",
                            on_click=AuthState.logout,
                            variant="ghost",
                            size="1",
                            width="100%",
                        ),
                        padding_x="3",
                        padding_bottom="3",
                    ),
                    spacing="1",
                    width="100%",
                ),
                width="100%",
            ),
            height="100vh",
            width="220px",
            spacing="0",
        ),
        border_right="1px solid var(--gray-5)",
        height="100vh",
        position="fixed",
        left="0",
        top="0",
    )
