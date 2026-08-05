"""Config fixtures for the knowledge-layer tests.

`index_payload` is a plain dict rather than a built model on purpose: a test
that needs a different root list, a longer `structural_keys`, or the in-memory
backend mutates two lines of the dict and revalidates, which keeps the
validators in the loop. A pre-built frozen model would force `model_copy`, which
skips validation and would let a test assert against a config the loader would
have rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m365_brain.config.index import FrontmatterConfig, IndexConfig, ObservationConfig, RelationConfig

# The four universal frontmatter keys. Any corpus-specific vocabulary is the
# consuming config's business, so tests that care about promotion extend this
# list themselves rather than relying on a fixture that grew someone's
# conventions.
UNIVERSAL_STRUCTURAL_KEYS = ["title", "type", "permalink", "tags"]


def index_payload_for(database_path: Path, roots: list[dict[str, object]]) -> dict[str, object]:
    """A complete `index:` payload. Every key present, nothing defaulted."""
    return {
        "backend": "sqlite",
        "sqlite": {"path": str(database_path), "busy_timeout_ms": 5000, "journal_mode": "WAL"},
        "roots": roots,
        "file_extensions": [".md"],
        "exclude": [],
        "sync": {"batch_size": 2, "interval_minutes": 60},
        "frontmatter": {
            "title_key": "title",
            "type_key": "type",
            "permalink_key": "permalink",
            "tags_key": "tags",
            "aliases_key": "aliases",
            "default_type": "note",
            "structural_keys": list(UNIVERSAL_STRUCTURAL_KEYS),
        },
        "observations": {"default_category": "Note"},
        "relations": {"explicit_default_type": "relates_to", "inline_type": "links_to"},
        "search": {
            "page_size": 20,
            "bm25_weights": {"title": 10.0, "content": 1.0, "tags": 5.0},
            "snippet": {
                "column": "content",
                "start_marker": ">>>",
                "end_marker": "<<<",
                "ellipsis": "...",
                "max_tokens": 40,
            },
            "rrf_k": 60,
            "rrf_min_weight": 0.1,
            "vector_candidates": 100,
            "min_similarity": 0.55,
        },
        "catalog": {
            "conversion_states": ["pending", "eager", "converted", "failed", "skipped"],
            "initial_state": "pending",
        },
        "vector": {
            "enabled": True,
            "provider": "hash",
            "store": "memory",
            "model": "test-model",
            "dimensions": 8,
            "threads": 1,
            "chunk_size": 900,
            "chunk_overlap": 120,
            "embed_batch_size": 32,
            "write_batch_size": 50,
        },
    }


@pytest.fixture()
def corpus_root(tmp_path) -> Path:
    """One root directory holding markdown, created empty."""
    root = tmp_path / "corpus"
    root.mkdir()
    return root


@pytest.fixture()
def index_payload(tmp_path, corpus_root) -> dict[str, object]:
    return index_payload_for(
        tmp_path / "index.db",
        [{"name": "corpus", "path": str(corpus_root), "recursive": True}],
    )


@pytest.fixture()
def index_config(index_payload) -> IndexConfig:
    return IndexConfig.model_validate(index_payload)


@pytest.fixture()
def frontmatter_config(index_config) -> FrontmatterConfig:
    return index_config.frontmatter


@pytest.fixture()
def observation_config(index_config) -> ObservationConfig:
    return index_config.observations


@pytest.fixture()
def relation_config(index_config) -> RelationConfig:
    return index_config.relations
