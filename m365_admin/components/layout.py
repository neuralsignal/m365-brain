"""Page layout wrapper with sidebar and auth guard."""

import reflex as rx

from m365_admin.components.sidebar import sidebar


def page_layout(content: rx.Component) -> rx.Component:
    """Wrap page content with sidebar navigation.

    Usage in page definitions:
        return page_layout(rx.vstack(...))
    """
    return rx.flex(
        sidebar(),
        rx.box(
            content,
            margin_left="220px",
            width="100%",
            min_height="100vh",
        ),
        width="100%",
    )
