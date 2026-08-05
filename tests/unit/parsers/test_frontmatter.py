"""Ported from the source package's frontmatter suite; `extract_tags` gained a config."""

from __future__ import annotations

from m365_brain.parsers.frontmatter import extract_tags, parse_frontmatter


def test_basic_yaml_frontmatter():
    text = "---\ntitle: Hello\ntype: note\n---\nBody"
    meta, body = parse_frontmatter(text)
    assert meta["title"] == "Hello"
    assert meta["type"] == "note"
    assert body == "Body"


def test_no_frontmatter():
    text = "Just plain text"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == "Just plain text"


def test_malformed_yaml():
    text = "---\nkey: [unclosed\n---\nBody"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_unclosed_block_is_all_body():
    text = "---\ntitle: Hello\nno closing delimiter"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_scalar_yaml_is_not_metadata():
    text = "---\njust a string\n---\nBody"
    meta, body = parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_bom_prefix():
    text = "﻿---\ntitle: Hello\ntype: note\n---\nBody"
    meta, body = parse_frontmatter(text)
    assert meta["title"] == "Hello"
    assert meta["type"] == "note"
    assert body == "Body"


def test_extract_tags_string(frontmatter_config):
    assert extract_tags({"tags": "a, b"}, frontmatter_config) == ["a", "b"]


def test_extract_tags_list(frontmatter_config):
    assert extract_tags({"tags": ["a", "b"]}, frontmatter_config) == ["a", "b"]


def test_extract_tags_missing(frontmatter_config):
    assert extract_tags({}, frontmatter_config) == []


def test_extract_tags_reads_the_configured_key(index_payload):
    from m365_brain.config.index import IndexConfig

    index_payload["frontmatter"]["tags_key"] = "keywords"
    config = IndexConfig.model_validate(index_payload).frontmatter
    assert extract_tags({"keywords": ["a"], "tags": ["ignored"]}, config) == ["a"]


def test_numbers_and_booleans_normalise_to_strings():
    text = "---\npriority: 3\nactive: true\n---\nBody"
    meta, _ = parse_frontmatter(text)
    assert meta["priority"] == "3"
    assert meta["active"] == "True"
