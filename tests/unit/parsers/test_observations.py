"""Ported from the source package's observation suite; gained a config fixture."""

from __future__ import annotations

from m365_brain.config.index import IndexConfig
from m365_brain.parsers.observations import parse_observations


def test_well_formed(observation_config):
    result = parse_observations("- [Role] Engineer", observation_config)
    assert len(result) == 1
    assert result[0].category == "Role"
    assert result[0].content == "Engineer"


def test_empty_category(observation_config):
    result = parse_observations("- [] Something", observation_config)
    assert len(result) == 1
    assert result[0].category == "Note"
    assert result[0].content == "Something"


def test_task_items_skipped(observation_config):
    assert parse_observations("- [x] Done", observation_config) == []


def test_wikilinks_skipped(observation_config):
    assert parse_observations("- [[Some Entity]]", observation_config) == []


def test_markdown_links_skipped(observation_config):
    assert parse_observations("- [label](https://example.com)", observation_config) == []


def test_inline_tags_extracted(observation_config):
    result = parse_observations("- [Note] something #important", observation_config)
    assert len(result) == 1
    assert result[0].tags == ["important"]


def test_context_extraction(observation_config):
    result = parse_observations("- [Note] something (in 2024)", observation_config)
    assert len(result) == 1
    assert result[0].context == "in 2024"


def test_parenthesised_wikilink_is_not_context(observation_config):
    result = parse_observations("- [Note] something (see [[Other]])", observation_config)
    assert len(result) == 1
    assert result[0].context is None


def test_untagged_prose_bullet_is_not_an_observation(observation_config):
    assert parse_observations("- just a sentence", observation_config) == []


def test_tagged_prose_bullet_uses_the_default_category(observation_config):
    result = parse_observations("- plain text #topic", observation_config)
    assert len(result) == 1
    assert result[0].category == "Note"


def test_default_category_comes_from_config(index_payload):
    index_payload["observations"]["default_category"] = "Fact"
    config = IndexConfig.model_validate(index_payload).observations
    result = parse_observations("- [] Something", config)
    assert result[0].category == "Fact"
