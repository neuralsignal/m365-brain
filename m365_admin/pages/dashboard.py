"""Dashboard page — per-extractor sync status grid and quick actions."""

import reflex as rx

from m365_admin.auth_state import AuthState
from m365_admin.components.layout import page_layout
from m365_admin.sync_state import SyncState


def _status_badge(status: rx.Var) -> rx.Component:
    """Render a colored badge based on extractor status."""
    return rx.cond(
        status == "success",
        rx.badge("Success", color_scheme="green"),
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


def _extractor_row(entry: rx.Var) -> rx.Component:
    """Render a single extractor status row."""
    return rx.table.row(
        rx.table.cell(rx.text(entry["name"], weight="bold")),
        rx.table.cell(_status_badge(entry["status"])),
        rx.table.cell(entry["last_run_at"]),
        rx.table.cell(entry["items_synced"]),
        rx.table.cell(
            rx.cond(
                entry["error"] != "",
                rx.text(entry["error"], color="red", size="1"),
                rx.text("—", color="gray"),
            )
        ),
    )


def _extractor_status_grid() -> rx.Component:
    """Grid showing per-extractor sync status."""
    return rx.cond(
        SyncState.extractor_statuses.length() > 0,
        rx.box(
            rx.heading("Extractor Status", size="4"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Extractor"),
                        rx.table.column_header_cell("Status"),
                        rx.table.column_header_cell("Last Run"),
                        rx.table.column_header_cell("Items"),
                        rx.table.column_header_cell("Error"),
                    ),
                ),
                rx.table.body(
                    rx.foreach(SyncState.extractor_statuses, _extractor_row),
                ),
                width="100%",
            ),
            width="100%",
        ),
        rx.text("No sync data yet. Enable extractors in Settings and the worker will start syncing.", color="gray"),
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
                _quick_actions(),
                _extractor_status_grid(),
                spacing="6",
                width="100%",
                max_width="900px",
            ),
            padding_top="8",
            padding_x="4",
        ),
    )
