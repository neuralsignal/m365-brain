"""Every `m365-brain` command the README prints is a command that exists.

All four in the Quick Start did not. `sync --once`, `sync --continuous` and
`sync --once --extractors email,calendar` name a verb that was never
registered -- the spellings are `run --once`, `run` and `run --once --only` --
and `auth login` without `--profile` exits 2, because the option is required.
A reader following the first page of the documentation got four usage errors
before reaching anything the package does.

Prose drifts silently; a command line does not have to. This parses the README
itself and puts every invocation through the real CLI, so the same drift fails
here instead of in front of the next person who reads it.

**What this checks, and what it deliberately does not.** Each command runs
against a config path that does not exist, inside an isolated filesystem. The
argument parse therefore happens in full -- unknown verb, unknown option and
missing required option all surface -- and then the verb exits 3 on the missing
config before it can reach Graph, the network, or the caller's disk. It is a
test that the command line is *accepted*, not that the sync works; the second
is what `tests/integration/test_independence.py` is for.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_USAGE

README = Path(__file__).resolve().parents[2] / "README.md"
CONSOLE_SCRIPT = "m365-brain"
FENCE = "```"


def _commands(markdown: str) -> list[str]:
    """Every `m365-brain …` line inside a fenced code block.

    Fenced only: a command named in prose is being discussed, not offered, and
    the `m365-brain/` in the project-structure tree is a directory.
    """
    found: list[str] = []
    inside = False
    for line in markdown.splitlines():
        if line.startswith(FENCE):
            inside = not inside
            continue
        if inside and line.strip().startswith(f"{CONSOLE_SCRIPT} "):
            found.append(line.strip())
    return found


README_COMMANDS = _commands(README.read_text(encoding="utf-8"))


def test_the_readme_shows_commands_at_all():
    """A parser that silently matched nothing would pass every test below."""
    assert len(README_COMMANDS) >= 4, "the Quick Start alone should offer more than this"


@pytest.mark.parametrize("command", README_COMMANDS)
def test_the_cli_accepts_it(command):
    runner = CliRunner()
    argv = shlex.split(command)[1:]
    with runner.isolated_filesystem():
        result = runner.invoke(main, argv)

    assert result.exit_code != EXIT_USAGE, f"`{command}` is not a command this CLI accepts:\n{result.output}"
    assert "No such command" not in result.output, result.output
    assert "no such option" not in result.output.lower(), result.output
