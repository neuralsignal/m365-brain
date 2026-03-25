"""Dashboard page — sync status overview and quick actions."""

import reflex as rx

from m365_admin.auth_state import AuthState
from m365_admin.components.layout import page_layout
from m365_admin.sync_state import SyncState


def _status_badge(status: rx.Var) -> rx.Component:
    """Render a colored badge based on sync status."""
    return rx.cond(
        status == "completed",
        rx.badge("Completed", color_scheme="green"),
        rx.cond(
            status == "running",
            rx.badge("Running", color_scheme="blue"),
            rx.cond(
                status == "failed",
                rx.badge("Failed", color_scheme="red"),
                rx.badge(status, color_scheme="gray"),
            ),
        ),
    )


def _info_row(label: str, value: rx.Component) -> rx.Component:
    return rx.flex(
        rx.text(label, weight="bold", width="120px"),
        value,
        spacing="2",
        align="center",
    )


def _sync_status_card() -> rx.Component:
    """Card showing the latest sync status."""
    return rx.card(
        rx.vstack(
            rx.heading("Latest Sync", size="4"),
            rx.separator(),
            _info_row("Status", _status_badge(SyncState.latest_sync_status)),
            _info_row("Time", rx.text(SyncState.latest_sync_time)),
            _info_row("Items Synced", rx.text(SyncState.latest_sync_items)),
            _info_row("Extractors", rx.text(SyncState.latest_sync_extractors)),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def _history_row(record: rx.Var) -> rx.Component:
    """Render a single sync history row."""
    return rx.table.row(
        rx.table.cell(record["started_at"]),
        rx.table.cell(_status_badge(record["status"])),
        rx.table.cell(record["extractors"]),
        rx.table.cell(record["items_synced"]),
        rx.table.cell(
            rx.cond(
                record["error"] != "",
                rx.text(record["error"], color="red", size="1"),
                rx.text("—", color="gray"),
            )
        ),
    )


def _sync_history_table() -> rx.Component:
    """Table showing recent sync runs."""
    return rx.cond(
        SyncState.sync_history.length() > 0,
        rx.box(
            rx.heading("Sync History", size="4"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Started"),
                        rx.table.column_header_cell("Status"),
                        rx.table.column_header_cell("Extractors"),
                        rx.table.column_header_cell("Items"),
                        rx.table.column_header_cell("Error"),
                    ),
                ),
                rx.table.body(
                    rx.foreach(SyncState.sync_history, _history_row),
                ),
                width="100%",
            ),
            width="100%",
        ),
        rx.text("No sync history yet. The sync daemon will populate this.", color="gray"),
    )


def _quick_actions() -> rx.Component:
    """Quick action links."""
    return rx.card(
        rx.vstack(
            rx.heading("Quick Actions", size="4"),
            rx.separator(),
            rx.link("Extractor Settings", href="/settings"),
            rx.cond(
                AuthState.is_admin,
                rx.link("Admin Panel", href="/admin"),
                rx.fragment(),
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


@rx.page(
    route="/dashboard",
    title="Dashboard — m365 Admin",
    on_load=[AuthState.check_auth, SyncState.load_sync_status],
)
def dashboard_page() -> rx.Component:
    return page_layout(
        rx.center(
            rx.vstack(
                rx.heading("Dashboard", size="5"),
                rx.flex(
                    _sync_status_card(),
                    _quick_actions(),
                    spacing="4",
                    width="100%",
                    flex_wrap="wrap",
                ),
                _sync_history_table(),
                spacing="6",
                width="100%",
                max_width="900px",
            ),
            padding_top="8",
            padding_x="4",
        ),
    )
