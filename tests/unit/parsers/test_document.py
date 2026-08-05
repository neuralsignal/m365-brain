"""Ported from the source package's parse-file suite.

Assertions moved from `dict[...]` to `Entity` attributes, and the "no custom
metadata" case now expects `{}` rather than `None` -- `Entity.metadata` is a
`dict`, so absence is an empty mapping and no caller has to test for None.
"""

from __future__ import annotations

import datetime
import json

from m365_brain.config.index import IndexConfig
from m365_brain.parsers.document import _json_safe, parse_markdown_file


class TestJsonSafe:
    """YAML-parsed types coerce to JSON-serialisable values."""

    def test_date_becomes_iso_string(self):
        assert _json_safe(datetime.date(2026, 2, 24)) == "2026-02-24"

    def test_datetime_becomes_iso_string(self):
        assert _json_safe(datetime.datetime(2026, 2, 24, 10, 30, 0)) == "2026-02-24T10:30:00"

    def test_string_unchanged(self):
        assert _json_safe("hello") == "hello"

    def test_int_unchanged(self):
        assert _json_safe(42) == 42

    def test_bool_unchanged(self):
        assert _json_safe(True) is True

    def test_none_unchanged(self):
        assert _json_safe(None) is None

    def test_nested_list(self):
        assert _json_safe([datetime.date(2026, 1, 1), "keep", 42]) == ["2026-01-01", "keep", 42]

    def test_nested_dict(self):
        value = {"start": datetime.date(2026, 1, 1), "name": "test"}
        assert _json_safe(value) == {"start": "2026-01-01", "name": "test"}

    def test_result_is_json_serializable(self):
        value = {
            "date": datetime.date(2026, 2, 24),
            "timestamp": datetime.datetime(2026, 2, 24, 10, 30),
            "items": [datetime.date(2025, 12, 31)],
            "nested": {"inner_date": datetime.date(2024, 6, 15)},
        }
        json.dumps(_json_safe(value))  # must not raise


class TestMetadata:
    def test_date_in_frontmatter_serializable(self, corpus_root, index_config):
        path = corpus_root / "test.md"
        path.write_text("---\ntitle: Test\ntype: note\ndate: 2026-02-24\n---\nBody\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.metadata["date"] == "2026-02-24"
        json.dumps(entity.metadata)  # must not raise

    def test_datetime_in_frontmatter_serializable(self, corpus_root, index_config):
        path = corpus_root / "test.md"
        path.write_text("---\ntitle: Test\ntype: note\ncreated: 2026-02-24 10:30:00\n---\nBody\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        json.dumps(entity.metadata)  # must not raise

    def test_no_custom_metadata_is_empty(self, corpus_root, index_config):
        path = corpus_root / "test.md"
        path.write_text("---\ntitle: Test\ntype: note\n---\nBody\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.metadata == {}


class TestAliases:
    """`aliases` is lifted out of `metadata` into a typed column, under a configured key."""

    def test_default_key(self, corpus_root, index_config):
        path = corpus_root / "a.md"
        path.write_text("---\naliases: [AC, Acme]\n---\nbody\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.aliases == ["AC", "Acme"]

    def test_a_scalar_becomes_a_single_alias(self, corpus_root, index_config):
        path = corpus_root / "b.md"
        path.write_text("---\naliases: AC\n---\nbody\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.aliases == ["AC"]

    def test_the_key_is_configurable(self, corpus_root, index_payload):
        """A vault calling the field `also-known-as` must work with no code change.

        That is the whole point of the config root: the conventional key name is
        a default someone chose, not a contract the library imposes.
        """
        index_payload["frontmatter"]["aliases_key"] = "also-known-as"
        config = IndexConfig.model_validate(index_payload)
        path = corpus_root / "c.md"
        path.write_text("---\nalso-known-as: [AC]\naliases: [ignored]\n---\nbody\n", encoding="utf-8")
        entity = parse_markdown_file(path, config.roots[0], config)
        assert entity is not None
        assert entity.aliases == ["AC"]


class TestIdentity:
    def test_key_carries_the_root_name(self, corpus_root, index_config):
        (corpus_root / "projects").mkdir()
        path = corpus_root / "projects" / "x.md"
        path.write_text("body\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.key == "corpus/projects/x.md"
        assert entity.root_name == "corpus"
        assert entity.file_path == "projects/x.md"

    def test_defaults_come_from_the_filename_and_config(self, corpus_root, index_config):
        path = corpus_root / "Some Note.md"
        path.write_text("no frontmatter\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.title == "Some Note"
        assert entity.entity_type == "note"
        assert entity.permalink == "some-note"

    def test_content_is_title_then_body(self, corpus_root, index_config):
        path = corpus_root / "n.md"
        path.write_text("---\ntitle: T\n---\nBody line\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert entity.content == "T\nBody line\n"

    def test_observations_and_relations_are_parsed(self, corpus_root, index_config):
        path = corpus_root / "n.md"
        path.write_text("- [Role] Engineer\n- works_at [[Acme]]\n", encoding="utf-8")
        entity = parse_markdown_file(path, index_config.roots[0], index_config)
        assert entity is not None
        assert [o.category for o in entity.observations] == ["Role"]
        assert [r.to_name for r in entity.relations] == ["Acme"]

    def test_unreadable_file_returns_none(self, corpus_root, index_config):
        missing = corpus_root / "gone.md"
        assert parse_markdown_file(missing, index_config.roots[0], index_config) is None
