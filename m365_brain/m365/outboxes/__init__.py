"""The Microsoft 365 executors, and the one function that builds all of them.

`build_handlers` is the bridge. It is deliberately *here* rather than inside
`outbox/`: the lifecycle and the executors are peers in the layer map, so
exactly one of them has to name the other, and the executor half is the one
that already knows both the payload shapes (from `vault`) and the transport.
The caller passes the result to `outbox.build_registry`, which is where the
tier guards run.
"""

from __future__ import annotations

from m365_brain.config import OutboxesConfig, UploadConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.outboxes.email import EmailOutbox, load_signature_html
from m365_brain.m365.outboxes.files import FILE_UPDATE_KIND, FileUpdateOutbox
from m365_brain.m365.outboxes.teams import TEAMS_POST_KIND, TeamsPostOutbox
from m365_brain.vault.dispatch import OutboxHandler

EMAIL_KINDS = ("email.draft", "email.reply", "email.forward")

__all__ = [
    "EmailOutbox",
    "FileUpdateOutbox",
    "TeamsPostOutbox",
    "build_handlers",
    "load_signature_html",
]


def build_handlers(
    config: OutboxesConfig,
    upload: UploadConfig,
    clients: dict[str, GraphClient],
) -> dict[str, OutboxHandler]:
    """One handler per configured outbox, each bound to its profile's client.

    `clients` is keyed by auth-profile name, so an outbox dispatches through
    the Entra app its config names and no other. That pairing is what makes
    the `draft_only` scope guard mean anything: a handler physically cannot
    reach an app whose scopes it was not granted.
    """
    signature_html = load_signature_html(config.email.signature)
    handlers: dict[str, OutboxHandler] = {}
    for name, definition in config.definitions.items():
        client = clients.get(definition.auth_profile)
        if client is None:
            raise KeyError(
                f"outbox {name!r} names auth profile {definition.auth_profile!r}, "
                f"for which no client was supplied (have: {sorted(clients)})"
            )
        handlers[name] = _handler(name, client, upload, config, signature_html)
    return handlers


def _handler(
    name: str,
    client: GraphClient,
    upload: UploadConfig,
    config: OutboxesConfig,
    signature_html: str,
) -> OutboxHandler:
    if name in EMAIL_KINDS:
        return EmailOutbox(
            name=name,
            client=client,
            upload=upload,
            attachment_root=config.attachment_root,
            signature=config.email.signature,
            signature_html=signature_html,
        )
    if name == TEAMS_POST_KIND:
        return TeamsPostOutbox(name=name, client=client)
    if name == FILE_UPDATE_KIND:
        return FileUpdateOutbox(name=name, client=client, upload=upload)
    raise KeyError(
        f"outboxes.definitions names {name!r}, which no executor implements. "
        f"Known outboxes: {[*EMAIL_KINDS, TEAMS_POST_KIND, FILE_UPDATE_KIND]}"
    )
