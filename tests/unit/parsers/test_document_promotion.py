"""Frontmatter promotion -- ported from the source package's promotion suite.

The source imported a 24-entry `STRUCTURAL_FM_KEYS` frozenset, half of which was
one corpus's email and chat vocabulary. That list is now required config with no
library default, so these tests assert the *mechanism* -- whatever the config
blocks is not promoted, whatever it does not block is -- rather than pinning a
particular vocabulary into the library's own suite. The list itself belongs in
the consuming config, next to whatever writes those keys.
"""

from __future__ import annotations

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.parsers.document import parse_markdown_file

# A blocklist of this test's own invention: the four universal keys plus three
# that any corpus might carry. It is deliberately not anyone's real list.
BLOCKED_KEYS = ["title", "type", "permalink", "tags", "aliases", "source", "annotations"]


@pytest.fixture()
def promotion_config(index_payload) -> IndexConfig:
    index_payload["frontmatter"]["structural_keys"] = list(BLOCKED_KEYS)
    return IndexConfig.model_validate(index_payload)


def _parse(corpus_root, config, text: str, name: str = "note.md"):
    path = corpus_root / name
    path.write_text(text, encoding="utf-8")
    entity = parse_markdown_file(path, config.roots[0], config)
    assert entity is not None
    return entity


def test_promoted_frontmatter_becomes_observation(corpus_root, promotion_config):
    entity = _parse(
        corpus_root,
        promotion_config,
        "---\ntitle: Alice\ntype: person\nemail: alice@example.com\nregion: north\n---\n# Alice\n",
    )
    categories = {o.category for o in entity.observations}
    assert "email" in categories
    assert "region" in categories
    email = next(o for o in entity.observations if o.category == "email")
    assert email.content == "alice@example.com"


def test_body_observation_takes_precedence(corpus_root, promotion_config):
    entity = _parse(
        corpus_root,
        promotion_config,
        "---\ntitle: Alice\ntype: person\nemail: alice@example.com\n---\n"
        "# Alice\n\n## Observations\n\n- [email] alice@example.com (work)\n",
    )
    email = [o for o in entity.observations if o.category == "email"]
    assert len(email) == 1
    assert email[0].context == "work"


def test_configured_structural_keys_are_never_promoted(corpus_root, promotion_config):
    entity = _parse(
        corpus_root,
        promotion_config,
        "---\ntitle: Test\ntype: note\npermalink: test-note\ntags: [test]\n"
        "source:\n  system: test\nannotations: []\n---\n# Test\n",
    )
    categories = {o.category for o in entity.observations}
    assert categories.isdisjoint(BLOCKED_KEYS)


def test_a_key_outside_the_blocklist_is_promoted(corpus_root, promotion_config):
    entity = _parse(
        corpus_root, promotion_config, "---\ntitle: My Goal\ntype: goal\nstatus: in_progress\n---\n# Goal\n"
    )
    status = [o for o in entity.observations if o.category == "status"]
    assert len(status) == 1
    assert status[0].content == "in_progress"


def test_extending_the_blocklist_stops_promotion(corpus_root, index_payload):
    index_payload["frontmatter"]["structural_keys"] = [*BLOCKED_KEYS, "status"]
    config = IndexConfig.model_validate(index_payload)
    entity = _parse(corpus_root, config, "---\ntitle: G\ntype: goal\nstatus: in_progress\n---\n# G\n")
    assert "status" not in {o.category for o in entity.observations}


def test_dict_values_not_promoted(corpus_root, promotion_config):
    entity = _parse(
        corpus_root,
        promotion_config,
        "---\ntitle: Test\ntype: note\norigin:\n  system: s\n  id: abc\n---\n# Test\n",
    )
    assert "origin" not in {o.category for o in entity.observations}


def test_list_values_not_promoted(corpus_root, promotion_config):
    entity = _parse(
        corpus_root, promotion_config, "---\ntitle: Test\ntype: note\nowners:\n  - Bo\n  - Al\n---\n# Test\n"
    )
    assert "owners" not in {o.category for o in entity.observations}


def test_none_values_not_promoted(corpus_root, promotion_config):
    entity = _parse(corpus_root, promotion_config, "---\ntitle: Test\ntype: note\nemail:\n---\n# Test\n")
    assert "email" not in {o.category for o in entity.observations}


def test_numeric_value_promoted_as_string(corpus_root, promotion_config):
    entity = _parse(corpus_root, promotion_config, "---\ntitle: Test\ntype: note\npriority: 3\n---\n# Test\n")
    priority = [o for o in entity.observations if o.category == "priority"]
    assert len(priority) == 1
    assert priority[0].content == "3"


def test_boolean_value_promoted_as_string(corpus_root, promotion_config):
    entity = _parse(corpus_root, promotion_config, "---\ntitle: Test\ntype: note\nis_recurring: true\n---\n# Test\n")
    recurring = [o for o in entity.observations if o.category == "is_recurring"]
    assert len(recurring) == 1
    assert recurring[0].content == "True"


def test_aliases_are_typed_and_kept_in_metadata(corpus_root, promotion_config):
    entity = _parse(
        corpus_root,
        promotion_config,
        "---\ntitle: Alice Smith\ntype: person\naliases:\n  - Alice\n  - AS\n---\n# Alice\n",
    )
    assert entity.aliases == ["Alice", "AS"]
    # Still in metadata too: the backends that can query inside JSON resolve an
    # alias there, and dropping it would silently break that lookup.
    assert entity.metadata["aliases"] == ["Alice", "AS"]


def test_promoted_observations_carry_no_tags_or_context(corpus_root, promotion_config):
    entity = _parse(corpus_root, promotion_config, "---\ntitle: T\ntype: note\nowner: bo\n---\n# T\n")
    owner = next(o for o in entity.observations if o.category == "owner")
    assert owner.tags == []
    assert owner.context is None
