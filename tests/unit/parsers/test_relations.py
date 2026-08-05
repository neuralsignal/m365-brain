"""Ported from the source package's relation suite; gained a config fixture."""

from __future__ import annotations

from m365_brain.config.index import IndexConfig
from m365_brain.parsers.relations import parse_relations


def test_explicit_wikilink_relation(relation_config):
    result = parse_relations("- works_at [[Acme]]", relation_config)
    assert len(result) == 1
    assert result[0].relation_type == "works_at"
    assert result[0].to_name == "Acme"


def test_bare_wikilink(relation_config):
    result = parse_relations("- [[Acme]]", relation_config)
    assert len(result) == 1
    assert result[0].relation_type == "relates_to"
    assert result[0].to_name == "Acme"


def test_inline_wikilink(relation_config):
    result = parse_relations("She works at [[Acme]] in Zurich", relation_config)
    assert len(result) == 1
    assert result[0].relation_type == "links_to"
    assert result[0].to_name == "Acme"


def test_context(relation_config):
    result = parse_relations("- knows [[Bob]] (from college)", relation_config)
    assert len(result) == 1
    assert result[0].context == "from college"


def test_deduplication(relation_config):
    result = parse_relations("- works_at [[Acme]]\n- works_at [[Acme]]", relation_config)
    assert len(result) == 1


def test_task_item_with_a_link_is_not_a_relation(relation_config):
    assert parse_relations("- [x] ship [[Thing]]", relation_config) == []


def test_edges_are_unresolved(relation_config):
    result = parse_relations("- [[Acme]]", relation_config)
    assert result[0].to_entity_id is None


def test_explicit_beats_inline_for_the_same_target(relation_config):
    body = "- works_at [[Acme]]\n\nProse mentioning [[Acme]] again."
    result = parse_relations(body, relation_config)
    types = [relation.relation_type for relation in result]
    assert types == ["works_at", "links_to"]


def test_relation_types_come_from_config(index_payload):
    index_payload["relations"] = {"explicit_default_type": "about", "inline_type": "mentions"}
    config = IndexConfig.model_validate(index_payload).relations
    result = parse_relations("- [[Acme]]\n\nprose [[Other]]", config)
    assert result[0].relation_type == "about"
    assert result[1].relation_type == "mentions"
