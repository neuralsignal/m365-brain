"""Settings page — per-user extractor preferences."""

import reflex as rx

from m365_admin.auth_state import AuthState
from m365_admin.components.layout import page_layout
from m365_admin.preferences_state import PreferencesState


def _extractor_card(pref: rx.Var) -> rx.Component:
    """Render a single extractor preference card."""
    return rx.card(
        rx.flex(
            rx.vstack(
                rx.text(
                    rx.cond(
                        pref["extractor_name"] == "email",
                        "Email",
                        rx.cond(
                            pref["extractor_name"] == "calendar",
                            "Calendar",
                            rx.cond(
                                pref["extractor_name"] == "teams_chats",
                                "Teams Chats",
                                rx.cond(
                                    pref["extractor_name"] == "teams_channels",
                                    "Teams Channels",
                                    rx.cond(
                                        pref["extractor_name"] == "onedrive",
                                        "OneDrive",
                                        rx.cond(
                                            pref["extractor_name"] == "sharepoint",
                                            "SharePoint",
                                            rx.cond(
                                                pref["extractor_name"] == "contacts",
                                                "Contacts",
                                                "Directory",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    weight="bold",
                    size="3",
                ),
                rx.text(pref["extractor_name"], color="gray", size="1"),
                spacing="1",
            ),
            rx.spacer(),
            rx.switch(
                checked=pref["enabled"],
                on_change=lambda _val: PreferencesState.toggle_extractor(pref["extractor_name"]),
            ),
            align="center",
            width="100%",
        ),
        width="100%",
    )


@rx.page(
    route="/settings",
    title="Settings — m365 Admin",
    on_load=[AuthState.check_auth, PreferencesState.load_preferences],
)
def settings_page() -> rx.Component:
    return page_layout(
        rx.center(
            rx.vstack(
                rx.heading("Extractor Settings", size="5"),
                rx.text(
                    "Choose which Microsoft 365 data sources to sync.",
                    color="gray",
                ),
                rx.separator(),
                rx.foreach(
                    PreferencesState.preferences,
                    _extractor_card,
                ),
                spacing="4",
                width="100%",
                max_width="600px",
            ),
            padding_top="8",
            padding_x="4",
        ),
    )
