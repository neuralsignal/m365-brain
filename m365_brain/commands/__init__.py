"""Verb groups. Each module is option parsing, one library call, formatting.

No logic lives here. An `if` that is not about output shape belongs in the
library, where it can be tested without a `CliRunner`.
"""

from m365_brain.commands.auth import auth
from m365_brain.commands.config import config_group
from m365_brain.commands.files import files_group
from m365_brain.commands.index import index_group
from m365_brain.commands.ops import ops_group
from m365_brain.commands.outbox import outbox_group
from m365_brain.commands.teams import teams_group
from m365_brain.commands.vault import vault_group

__all__ = [
    "auth",
    "config_group",
    "files_group",
    "index_group",
    "ops_group",
    "outbox_group",
    "teams_group",
    "vault_group",
]
