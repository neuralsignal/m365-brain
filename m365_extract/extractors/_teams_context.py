"""Shared context dataclass for the Teams extractors.

Groups the cross-cutting concerns (client, storage, attachment settings,
converter config, skip-list, conversation directory) that flow together
through the Teams message-processing chain, so ``_teams_ingest``,
``_teams_attachment_helpers``, ``_teams_hosted_content``, ``teams_channels``,
and ``teams_chats`` pass one value instead of six.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_extract.extractors._teams_attachment_helpers import AttachmentSettings
from m365_extract.graph_client import GraphClient
from m365_extract.storage.base import StorageBackend


@dataclass(frozen=True)
class TeamsContext:
    """Per-conversation context for Teams message processing."""

    client: GraphClient
    storage: StorageBackend
    settings: AttachmentSettings
    converters_config: dict
    failed_attachments: dict[str, str]
    conv_dir: str
