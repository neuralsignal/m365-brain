#!/usr/bin/env python3
"""Reject identifiers that belong to a real person, tenant, or machine.

    python3 scripts/check_publishable.py           # list every finding
    python3 scripts/check_publishable.py --quiet   # exit code only

Exit 0 when clean, 1 otherwise.

This package is published. Three kinds of string routinely survive a manual
scrub because each one looks like scenery rather than data:

  * an email address  -- a fixture mailbox is still somebody's mailbox
  * a GUID            -- a tenant, subscription, or app registration id is a
                         durable pointer at one organisation's Azure estate
  * an absolute path  -- `/Users/<name>` names the author's laptop account

The check is deliberately entity-blind. Its predecessor spelled out the
private vocabulary it rejected, which meant shipping the check shipped the
vocabulary; a rule phrased as a shape instead of a name has nothing to leak
and keeps working after the next rename.

Every rule is an allow-list, so the failure mode is a false positive that
someone has to look at, never a silent pass. Adding an entry below is a
deliberate act -- the same reason `check_structure.py` makes an unmapped
subpackage a finding rather than a default.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# --- emails -----------------------------------------------------------------
#
# RFC 2606 / RFC 6761 reserve these for documentation and testing, so an
# address under one of them cannot route anywhere real. `contoso.com` is
# Microsoft's own published example tenant and is this repo's house style for
# Graph fixtures.
RESERVED_DOMAINS = frozenset({"example.com", "example.org", "example.net", "contoso.com"})
RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")

# Domains that predate this rule and are plainly not mailboxes. Kept as an
# explicit list rather than a cleverer predicate: a real leaked address must
# never be able to argue its way past a heuristic.
#
#   a.com / b.com / t.com / x.com / y.com   single-letter fixture domains
#   bar.com / company.com / secret.com      foo-bar placeholders in unit tests
#   test.com                                same, spelled out
#   thread.tacv                             Teams conversation ids contain `@`
#   odata.nextLink                          Graph OData annotation, not an address
#   account.dfs.core.windows.net            ADLS Gen2 URI, not an address
FIXTURE_DOMAINS = frozenset(
    {
        "a.com",
        "b.com",
        "t.com",
        "x.com",
        "y.com",
        "bar.com",
        "company.com",
        "secret.com",
        "test.com",
        "thread.tacv",
        "odata.nextLink",
        "account.dfs.core.windows.net",
    }
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def email_is_safe(domain: str) -> bool:
    lowered = domain.lower()
    if lowered in RESERVED_DOMAINS or domain in FIXTURE_DOMAINS:
        return True
    return lowered.endswith(RESERVED_TLDS)


# --- GUIDs ------------------------------------------------------------------
#
# A real GUID is random, so its 32 hex digits use most of the alphabet; a
# hand-written placeholder repeats a handful of characters or pads with zeros.
# Measured on this repo the two populations do not overlap: placeholders top
# out at 5 distinct digits (or are >=50% zeros), real Azure ids start at 13.
MAX_PLACEHOLDER_DIGITS = 6

GUID_RE = re.compile(r"\b([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\b")

# RFC 4122 Appendix C's example UUID. Universally recognised as documentation,
# and used as such by this repo's validation tests.
PLACEHOLDER_GUIDS = frozenset({"550e8400-e29b-41d4-a716-446655440000"})


def guid_is_safe(guid: str) -> bool:
    body = guid.replace("-", "").lower()
    if guid.lower() in PLACEHOLDER_GUIDS:
        return True
    return len(set(body)) <= MAX_PLACEHOLDER_DIGITS or body.count("0") * 2 >= len(body)


# --- home directories -------------------------------------------------------
#
# Generic account names that every reader substitutes for their own, plus the
# one GitHub Actions actually runs as.
PLACEHOLDER_USERS = frozenset({"user", "users", "username", "runner", "me", "you", "someone", "example", "test", "x"})

# The segment must open on something an account name can open on, which is
# what keeps `/c/Users/...` and `/mnt/c/Users/...` -- Windows path prose, with a
# literal ellipsis -- from reading as a person.
HOME_RE = re.compile(r"/(?:Users|home)/([A-Za-z0-9$<{][A-Za-z0-9._${}<>-]*)")


def home_is_safe(user: str) -> bool:
    # `$HOME`, `<user>`, `${USER}` are templates, not accounts.
    if any(ch in user for ch in "$<>{}"):
        return True
    return user.lower() in PLACEHOLDER_USERS


RULES: tuple[tuple[re.Pattern[str], object, str], ...] = (
    (EMAIL_RE, email_is_safe, "an email address at a domain that is not reserved for documentation"),
    (GUID_RE, guid_is_safe, "a GUID with the entropy of a real tenant, subscription, or app id"),
    (HOME_RE, home_is_safe, "an absolute path naming a real home directory"),
)

# Exemptions live here rather than in the invocation, so the exempt set is
# reviewable in the same place as the rules.
#
#   CHANGELOG.md   release-please owns it; release history is immutable
#   this file      its allow-lists necessarily spell out what they permit
#   its test file  its fixtures are, by construction, the violations themselves
SCRIPT_NAME = Path(__file__).name
TEST_NAME = f"test_{SCRIPT_NAME}"
EXEMPT_FILES = {"CHANGELOG.md", SCRIPT_NAME, TEST_NAME}
EXEMPT_DIR_PARTS = (
    ".git",
    ".pixi",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
    "node_modules",
    "__pycache__",
    ".web",
)

SCANNED_SUFFIXES = {
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".bicep", ".bicepparam",
    ".sh", ".cfg", ".ini", ".txt", ".example", ".Dockerfile",
}
SCANNED_NAMES = {"Dockerfile", ".env.example", ".dockerignore", ".gitignore"}

# Lock files are generated, enormous, and may legitimately contain a path
# fragment from whoever generated them.
EXEMPT_NAMES = {"pixi.lock", "uv.lock", "poetry.lock", "package-lock.json"}


def scannable(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    posix = path.relative_to(root).as_posix()
    if any(part in posix for part in EXEMPT_DIR_PARTS):
        return False
    if path.name in EXEMPT_FILES or path.name in EXEMPT_NAMES:
        return False
    return path.suffix in SCANNED_SUFFIXES or path.name in SCANNED_NAMES


def scan(root: Path) -> list[str]:
    findings = []
    for path in sorted(root.rglob("*")):
        if not scannable(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, is_safe, why in RULES:
                # group(1) is the part the rule judges -- domain, guid, account
                # name -- while group(0) is what the reader needs to see.
                hit = next((m for m in pattern.finditer(line) if not is_safe(m.group(1))), None)
                if hit:
                    rel = path.relative_to(root)
                    findings.append(f"{rel}:{number}: {why} -- {hit.group(0)}")
                    break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject real-world identifiers before publishing.")
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings = scan(root)

    if not args.quiet:
        for finding in findings:
            print(finding)
        print(
            f"\n{len(findings)} real-world identifier(s)"
            if findings
            else "clean: no real-world identifiers"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
