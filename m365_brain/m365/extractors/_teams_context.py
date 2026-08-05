"""Shared context dataclass for the Teams extractors.

Groups the cross-cutting concerns (client, storage, attachment settings,
converter config, skip-list, conversation directory) that flow together
through the Teams message-processing chain, so ``_teams_ingest``,
``_teams_attachment_helpers``, ``_teams_hosted_content``, ``teams_channels``,
and ``teams_chats`` pass one value instead of six.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors._teams_attachment_helpers import AttachmentSettings
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths


@dataclass(frozen=True)
class TeamsContext:
    """Per-conversation context for Teams message processing."""

    client: GraphClient
    storage: StorageBackend
    settings: AttachmentSettings
    converters_config: dict
    failed_attachments: dict[str, str]
    conv_dir: str
    paths: VaultPaths
    """Carried per-conversation because the attachment helpers build both an
    absolute storage key and the conversation-relative link the renderer
    emits, and those two must come from one resolver."""
