"""Hypothesis properties -- ported from the source package's property suite.

Configs are built at module level rather than taken as fixtures: `@given` and a
function-scoped fixture do not mix, and these two config objects are constants
of the test, not part of what is being varied.
"""

from __future__ import annotations

import string
import tempfile
from pathlib import Path

import yaml
from hypothesis import assume, given
from hypothesis import strategies as st

from m365_brain.config.index import ObservationConfig, RelationConfig
from m365_brain.parsers.frontmatter import parse_frontmatter
from m365_brain.parsers.observations import parse_observations
from m365_brain.parsers.relations import parse_relations
from m365_brain.parsers.text import file_checksum, slugify

OBSERVATION_CONFIG = ObservationConfig(default_category="Note")
RELATION_CONFIG = RelationConfig(explicit_default_type="relates_to", inline_type="links_to")


@given(st.text(min_size=0, max_size=200))
def test_slugify_returns_valid_chars(text):
    result = slugify(text)
    assert all(c in string.ascii_lowercase + string.digits + "-" for c in result)


@given(st.text(min_size=1, max_size=200, alphabet=string.ascii_letters + string.digits + " "))
def test_slugify_idempotent(text):
    first = slugify(text)
    assume(first)
    assert slugify(first) == first


@given(st.text(min_size=1, max_size=500, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))))
def test_parse_frontmatter_no_crash(text):
    meta, body = parse_frontmatter(text)
    assert isinstance(meta, dict)
    assert isinstance(body, str)


@given(st.text(min_size=0, max_size=500))
def test_parse_observations_returns_list(text):
    assert isinstance(parse_observations(text, OBSERVATION_CONFIG), list)


@given(st.binary(min_size=1, max_size=1000))
def test_file_checksum_deterministic(content):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_file"
        path.write_bytes(content)
        assert file_checksum(path) == file_checksum(path)


@given(st.binary(min_size=1, max_size=500), st.binary(min_size=1, max_size=500))
def test_file_checksum_different_content(content_a, content_b):
    assume(content_a != content_b)
    with tempfile.TemporaryDirectory() as tmp:
        path_a = Path(tmp) / "file_a"
        path_b = Path(tmp) / "file_b"
        path_a.write_bytes(content_a)
        path_b.write_bytes(content_b)
        assert file_checksum(path_a) != file_checksum(path_b)


@given(
    title=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + string.digits + " "),
    entity_type=st.sampled_from(["note", "person", "project", "meeting"]),
)
def test_parse_frontmatter_roundtrip(title, entity_type):
    # Dump the whole mapping: dumping a bare scalar emits a document-end marker.
    yaml_block = yaml.safe_dump({"title": title, "type": entity_type}, default_flow_style=False).strip()
    meta, body = parse_frontmatter(f"---\n{yaml_block}\n---\nBody text")
    assert meta["title"] == title
    assert meta["type"] == entity_type
    assert "Body text" in body


@given(st.text(min_size=0, max_size=500))
def test_parse_relations_never_emits_an_empty_type(text):
    """The `explicit_default_type` fallback covers every empty prefix.

    `- [[X]]`, `-  [[X]]`, and `- \t[[X]]` all reach the branch with nothing
    before the link; a fallback that only handled the first would emit an edge
    with a blank type, which no query can filter on.
    """
    for relation in parse_relations(text, RELATION_CONFIG):
        assert relation.relation_type
        assert relation.to_entity_id is None
