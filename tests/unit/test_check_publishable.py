"""The publishability checker must catch real identifiers without flagging documentation ones.

Every rule in the script is an allow-list, so both directions matter equally.
A rule that misses a real address ships somebody's mailbox; a rule that trips
on `someone@example.com` gets switched off within a week and then ships the
mailbox anyway. These tests pin both edges of each of the three rules.

The violations below are the shapes this repo actually leaked before the
scrub -- a tenant mailbox, an Azure tenant id, and an author's home directory.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_publishable.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_publishable", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cp = _load()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("A Microsoft 365 extraction package.\n")
    return tmp_path


def _write(tree: Path, name: str, text: str) -> None:
    (tree / "docs" / name).write_text(text)


# One violation per rule, in rule order.
VIOLATIONS = [
    "contact person.name@realcompany.com for access",
    "az login --tenant ea0bd7d3-b29f-47f4-aedc-da7b52a28ba0",
    "the vault lives at /Users/jdoe/Notes",
]


@pytest.mark.parametrize("line", VIOLATIONS)
def test_violation_is_caught(tree: Path, line: str):
    _write(tree, "leak.md", line + "\n")
    assert cp.scan(tree), f"missed: {line!r}"


def test_clean_file_is_clean(tree: Path):
    _write(
        tree,
        "clean.md",
        "mail someone@example.com, ai@contoso.com or a@x.test\n"
        "tenant 00000000-0000-0000-0000-000000000001\n"
        "the vault lives at /home/user/vault\n",
    )
    assert cp.scan(tree) == []


def test_clean_tree_is_clean(tree: Path):
    assert cp.scan(tree) == []


class TestEmailRule:
    @pytest.mark.parametrize(
        "line",
        [
            "someone@example.com",
            "ops@example.org",
            "ops@example.net",
            "bob@contoso.com",  # Microsoft's published example tenant
            "a@x.test",
            "operator@example.invalid",
            "dev@box.localhost",
        ],
    )
    def test_documentation_domains_pass(self, tree: Path, line: str):
        _write(tree, "safe.md", line + "\n")
        assert cp.scan(tree) == [], f"false positive on: {line!r}"

    @pytest.mark.parametrize(
        "line",
        ["ai@acme-corp.com", "jane.doe@hospital.ch", "noreply@some-tenant.onmicrosoft.com"],
    )
    def test_routable_domains_are_rejected(self, tree: Path, line: str):
        _write(tree, "leak.md", line + "\n")
        assert cp.scan(tree) != [], f"missed: {line!r}"

    def test_teams_conversation_ids_are_not_addresses(self, tree: Path):
        """A Teams chat id contains `@` and matches the address shape."""
        _write(tree, "ids.md", "chat id 19:abc123def456@thread.tacv\n")
        assert cp.scan(tree) == []


class TestGuidRule:
    @pytest.mark.parametrize(
        "line",
        [
            "00000000-0000-0000-0000-000000000000",
            "00000000-0000-4000-8000-000000000001",
            "11111111-2222-3333-4444-555555555555",
            "a1b2c3d4-0001-4000-8000-000000000001",
            "550e8400-e29b-41d4-a716-446655440000",  # RFC 4122 Appendix C
        ],
    )
    def test_placeholder_guids_pass(self, tree: Path, line: str):
        _write(tree, "safe.md", line + "\n")
        assert cp.scan(tree) == [], f"false positive on: {line!r}"

    @pytest.mark.parametrize(
        "line",
        [
            "9f079696-135d-4d18-a208-3e2e55fca2f5",
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "e31a8416-7cd9-4b71-9d7e-7f89cbd7631a",
        ],
    )
    def test_random_guids_are_rejected(self, tree: Path, line: str):
        _write(tree, "leak.md", line + "\n")
        assert cp.scan(tree) != [], f"missed: {line!r}"

    def test_case_does_not_change_the_verdict(self, tree: Path):
        _write(tree, "safe.md", "550E8400-E29B-41D4-A716-446655440000\n")
        assert cp.scan(tree) == []


class TestHomePathRule:
    @pytest.mark.parametrize(
        "line",
        [
            "/home/user/vault",
            "/Users/user/vault",
            "/home/runner/work/m365-brain",  # GitHub Actions
            "/Users/${USER}/vault",
            "/Users/<user>/vault",
            "/c/Users/... is a Windows path fragment",
            "/mnt/c/Users/... under WSL",
        ],
    )
    def test_placeholder_homes_pass(self, tree: Path, line: str):
        _write(tree, "safe.md", line + "\n")
        assert cp.scan(tree) == [], f"false positive on: {line!r}"

    @pytest.mark.parametrize("line", ["/Users/jdoe/Notes", "/home/asmith/vault", "/mnt/c/Users/jdoe/OneDrive"])
    def test_real_homes_are_rejected(self, tree: Path, line: str):
        _write(tree, "leak.md", line + "\n")
        assert cp.scan(tree) != [], f"missed: {line!r}"


class TestExemptions:
    def test_changelog_is_exempt(self, tree: Path):
        """release-please owns it and release history is immutable."""
        (tree / "CHANGELOG.md").write_text("* migrated off tenant ea0bd7d3-b29f-47f4-aedc-da7b52a28ba0\n")
        assert cp.scan(tree) == []

    def test_the_checker_itself_is_exempt(self):
        """Its allow-lists necessarily spell out what they permit."""
        assert cp.SCRIPT_NAME in cp.EXEMPT_FILES

    def test_this_test_file_is_exempt(self):
        """Its fixtures are, by construction, the violations themselves."""
        assert Path(__file__).name in cp.EXEMPT_FILES

    def test_lock_files_are_exempt(self, tree: Path):
        (tree / "pixi.lock").write_text("# built by /Users/jdoe/.pixi\n")
        assert cp.scan(tree) == []

    def test_unscanned_suffix_is_ignored(self, tree: Path):
        (tree / "docs" / "notes.rst").write_text("mail jane.doe@hospital.ch\n")
        assert cp.scan(tree) == []

    def test_binary_file_does_not_crash(self, tree: Path):
        (tree / "docs" / "blob.md").write_bytes(b"\xff\xfe\x00binary\x00")
        cp.scan(tree)  # must not raise


def test_finding_reports_path_line_and_match(tree: Path):
    _write(tree, "leak.md", "\n\nmail jane.doe@hospital.ch\n")
    (finding,) = cp.scan(tree)
    assert finding.startswith("docs/leak.md:3:")
    assert "jane.doe@hospital.ch" in finding
