"""Admin page — user management. Admin-only."""

import reflex as rx

from m365_admin.admin_state import AdminState
from m365_admin.auth_state import AuthState
from m365_admin.components.layout import page_layout


def _user_row(user: rx.Var) -> rx.Component:
    """Render a single user row."""
    return rx.table.row(
        rx.table.cell(user["display_name"]),
        rx.table.cell(user["email"]),
        rx.table.cell(
            rx.switch(
                checked=user["enabled"],
                on_change=lambda _val: AdminState.toggle_user_enabled(user["user_id"]),
            )
        ),
        rx.table.cell(user["last_sync"]),
        rx.table.cell(user["last_sync_status"]),
    )


def _access_denied() -> rx.Component:
    return page_layout(
        rx.center(
            rx.callout(
                "You do not have admin access.",
                icon="shield_alert",
                color_scheme="red",
            ),
            padding_top="8",
        ),
    )


@rx.page(
    route="/admin",
    title="Admin — m365 Admin",
    on_load=[AuthState.check_auth, AdminState.load_users],
)
def admin_page() -> rx.Component:
    return rx.cond(
        AuthState.is_admin,
        page_layout(
            rx.center(
                rx.vstack(
                    rx.heading("User Management", size="5"),
                    rx.text("Manage sync users and their access.", color="gray"),
                    rx.separator(),
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Name"),
                                rx.table.column_header_cell("Email"),
                                rx.table.column_header_cell("Enabled"),
                                rx.table.column_header_cell("Last Sync"),
                                rx.table.column_header_cell("Status"),
                            ),
                        ),
                        rx.table.body(
                            rx.foreach(AdminState.users, _user_row),
                        ),
                        width="100%",
                    ),
                    spacing="4",
                    width="100%",
                    max_width="900px",
                ),
                padding_top="8",
                padding_x="4",
            ),
        ),
        _access_denied(),
    )
