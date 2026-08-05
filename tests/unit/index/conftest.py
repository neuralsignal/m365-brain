"""Fixtures shared by the index tests.

`backend` is parametrized over every implementation of `IndexBackend`. A test
that needs to know which one it got has failed the point of the protocol -- the
two SQLite-only test modules construct their backend directly instead.

Entities are built with `dataclasses.replace` from one fully-specified constant
rather than a factory with default arguments: a factory would quietly supply the
values a test forgot, and a test that passes because of a helper's default is a
test of the helper.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import create_index_backend
from m365_brain.index.backends.base import IndexBackend, TextQuery
from m365_brain.model import CatalogEntry, CatalogQuery, Entity, Observation, Relation

BASE_ENTITY = Entity(
    key="corpus/note.md",
    root_name="corpus",
    file_path="note.md",
    title="Note",
    entity_type="note",
    permalink="note",
    tags=[],
    aliases=[],
    content="Note\nbody text",
    checksum="sum-0",
    metadata={},
    created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:00:00Z",
    observations=[],
    relations=[],
)

BASE_CATALOG_ENTRY = CatalogEntry(
    entry_id=None,
    original_path="drive/report.docx",
    file_name="report.docx",
    extension=".docx",
    source="drive",
    size_bytes=1024,
    modified_at="2026-01-01T00:00:00Z",
    conversion_status="pending",
    output_path=None,
    error=None,
)


def an_entity(**fields: Any) -> Entity:
    return replace(BASE_ENTITY, **fields)


def a_catalog_entry(**fields: Any) -> CatalogEntry:
    return replace(BASE_CATALOG_ENTRY, **fields)


def an_observation(category: str, content: str) -> Observation:
    return Observation(category=category, content=content, tags=[], context=None)


def a_relation(relation_type: str, to_name: str) -> Relation:
    return Relation(relation_type=relation_type, to_name=to_name, to_entity_id=None, context=None)


def a_text_query(**fields: Any) -> TextQuery:
    base = TextQuery(fts=None, entity_type=None, tag=None, metadata=(), page=1, page_size=20)
    return replace(base, **fields)


def a_catalog_query(**fields: Any) -> CatalogQuery:
    base = CatalogQuery(extension=None, source=None, status=None, modified_after=None, name_contains=None, limit=50)
    return replace(base, **fields)


def make_backend(index_payload: dict[str, Any], name: str) -> IndexBackend:
    index_payload["backend"] = name
    backend = create_index_backend(IndexConfig.model_validate(index_payload))
    backend.initialize()
    return backend


@pytest.fixture(params=["sqlite", "memory"])
def backend(request, index_payload) -> IndexBackend:
    instance = make_backend(index_payload, request.param)
    yield instance
    instance.close()


@pytest.fixture()
def sqlite_backend(index_payload) -> IndexBackend:
    instance = make_backend(index_payload, "sqlite")
    yield instance
    instance.close()
