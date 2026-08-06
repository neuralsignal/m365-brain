"""The facade: wiring, lifecycle, and delegation.

Every method on `Workspace` is one call to the module that owns the behaviour,
so these tests assert that the call happens with the right arguments -- not what
it does, which is tested where it lives. A facade test that re-tested search
would be the second copy of assertions that already exist.

The whole file runs on the in-memory backend and the offline embedder, so it
touches no disk beyond the config file and downloads nothing.
"""

from __future__ import annotations

import pytest
import yaml

from m365_brain.config import ConfigError
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.search import SearchFilters
from m365_brain.index.vector.memory import HashEmbeddingProvider, InMemoryVectorStore
from m365_brain.workspace import Workspace
from tests.unit.conftest import index_payload_for
from tests.unit.test_config_sections import _legacy_sections

NO_FILTERS = SearchFilters(entity_type=None, tag=None, metadata=())


def write_config(tmp_path, corpus_root, **overrides) -> str:
    """A complete config file whose `index:` names the in-memory backend."""
    index = index_payload_for(tmp_path / "index.db", [{"name": "corpus", "path": str(corpus_root), "recursive": True}])
    index["backend"] = "memory"
    for section, values in overrides.items():
        index[section].update(values)

    payload = _legacy_sections()
    payload["index"] = index
    path = tmp_path / "workspace.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return str(path)


def write_config_without_index(tmp_path) -> str:
    path = tmp_path / "no-index.yaml"
    path.write_text(yaml.safe_dump(_legacy_sections(), sort_keys=False), encoding="utf-8")
    return str(path)


@pytest.fixture()
def workspace(tmp_path, corpus_root) -> Workspace:
    with Workspace.open(write_config(tmp_path, corpus_root)) as handle:
        yield handle


def note(corpus_root, name: str, body: str) -> None:
    (corpus_root / f"{name}.md").write_text(body, encoding="utf-8")


# -- construction ---------------------------------------------------------


def test_open_builds_a_usable_handle(workspace):
    assert isinstance(workspace.backend, InMemoryIndexBackend)
    assert workspace.config.index is not None


def test_vectors_are_built_when_enabled(workspace):
    assert isinstance(workspace._provider, HashEmbeddingProvider)
    assert isinstance(workspace._store, InMemoryVectorStore)


def test_vectors_are_absent_when_disabled(tmp_path, corpus_root):
    with Workspace.open(write_config(tmp_path, corpus_root, vector={"enabled": False})) as handle:
        assert handle._provider is None
        assert handle._store is None


def test_a_config_without_an_index_section_raises(tmp_path):
    """Absence means "not in use"; using it anyway is a named crash, not a KeyError."""
    with pytest.raises(ConfigError, match="index"):
        Workspace.open(write_config_without_index(tmp_path))


def test_the_context_manager_closes_the_backend(tmp_path, corpus_root):
    closed: list[str] = []
    with Workspace.open(write_config(tmp_path, corpus_root)) as handle:
        handle._backend.close = lambda: closed.append("backend")
        handle._store.close = lambda: closed.append("store")
    assert closed == ["backend", "store"]


def test_close_is_safe_twice(workspace):
    workspace.close()
    workspace.close()


# -- delegation -----------------------------------------------------------


def test_sync_indexes_the_configured_roots(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\nrhubarb\n")
    stats = workspace.sync(full_rebuild=False)
    assert (stats.total, stats.indexed) == (1, 1)


def test_a_second_sync_skips_unchanged_files(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n")
    workspace.sync(full_rebuild=False)
    assert workspace.sync(full_rebuild=False).skipped == 1


def test_sync_vectors_embeds_what_the_index_holds(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\nrhubarb crumble\n")
    workspace.sync(full_rebuild=False)
    stats = workspace.sync_vectors(full_rebuild=False)
    assert stats.entities == 1
    assert stats.chunks_embedded == 1


def test_sync_vectors_raises_when_vectors_are_off(tmp_path, corpus_root):
    with (
        Workspace.open(write_config(tmp_path, corpus_root, vector={"enabled": False})) as handle,
        pytest.raises(ValueError, match="index.vector.enabled"),
    ):
        handle.sync_vectors(full_rebuild=False)


def test_search_returns_a_page(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\nrhubarb\n")
    workspace.sync(full_rebuild=False)
    page = workspace.search("rhubarb", mode="text", filters=NO_FILTERS, page=1, page_size=20)
    assert [hit.entity.key for hit in page.hits] == ["corpus/alpha.md"]


def test_search_pages_at_the_configured_size(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n")
    workspace.sync(full_rebuild=False)
    size = workspace.config.index.search.page_size
    page = workspace.search(None, mode="text", filters=NO_FILTERS, page=1, page_size=size)
    assert page.page_size == size


def test_hybrid_search_runs_end_to_end(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\nrhubarb crumble\n")
    workspace.sync(full_rebuild=False)
    workspace.sync_vectors(full_rebuild=False)
    assert workspace.search("rhubarb crumble", mode="hybrid", filters=NO_FILTERS, page=1, page_size=20).total == 1


def test_find_and_observations_reach_the_graph(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\n- [Fact] the sky is blue\n")
    workspace.sync(full_rebuild=False)

    entity = workspace.find("Alpha", by_permalink=False)

    assert entity is not None
    assert [o.content for o in workspace.observations(entity.entity_id)] == ["the sky is blue"]


def test_context_walks_the_graph(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n\nSee [[Beta]]\n")
    note(corpus_root, "beta", "# Beta\n")
    workspace.sync(full_rebuild=False)

    entity = workspace.find("Alpha", by_permalink=False)
    edges = workspace.context(entity.entity_id, max_depth=1)

    assert [edge.to_name for edge in edges] == ["Beta"]


def test_recent_accepts_a_timeframe(workspace, corpus_root):
    note(corpus_root, "alpha", "# Alpha\n")
    workspace.sync(full_rebuild=False)
    assert len(workspace.recent("7d", entity_type=None, limit=10)) == 1


def test_recent_total_counts_past_the_limit(workspace, corpus_root):
    """What makes `index recent`'s cap visible: the rows it did not return."""
    for name in ("alpha", "beta", "gamma"):
        note(corpus_root, name, f"# {name.title()}\n")
    workspace.sync(full_rebuild=False)

    assert len(workspace.recent("7d", entity_type=None, limit=1)) == 1
    assert workspace.recent_total("7d", entity_type=None) == 3


def test_an_unparseable_timeframe_raises(workspace):
    with pytest.raises(ValueError, match="cannot parse timeframe"):
        workspace.recent("whenever", entity_type=None, limit=10)


def test_catalog_is_bound_to_the_configured_states(workspace):
    catalog = workspace.catalog()
    assert isinstance(catalog, FileCatalog)
    assert catalog.initial_state == workspace.config.index.catalog.initial_state
