"""The `index:` section -- every knob the markdown index reads.

Nothing here has a default. The library ships no `structural_keys` list, no
embedding model name, and no database path: those are properties of the corpus
being indexed, not of the code that indexes it, and a library that supplies
them only works on the corpus its author had.

Lists are `list[...]` rather than `tuple[...]` because `strict=True` refuses to
widen a YAML sequence into a tuple. `frozen=True` still prevents rebinding the
field; the caller that wants set semantics builds the set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG


class IndexRoot(BaseModel):
    """One directory tree to index.

    `name` is not decoration: entity keys are `{name}/{relative path}`, so two
    roots may each hold `projects/x.md` without colliding on the unique
    file-path constraint. Names are validated unique for that reason.
    """

    model_config = SECTION_MODEL_CONFIG
    name: str
    path: str
    recursive: bool


class SqliteIndexConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    path: str
    busy_timeout_ms: int
    journal_mode: Literal["WAL", "DELETE"]


class IndexSyncConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    batch_size: int
    interval_minutes: int


class FrontmatterConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    title_key: str
    type_key: str
    permalink_key: str
    tags_key: str
    aliases_key: str
    default_type: str
    structural_keys: list[str]


class ObservationConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    default_category: str


class RelationConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    explicit_default_type: str
    inline_type: str


class Bm25Weights(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    title: float
    content: float
    tags: float


class SnippetConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    column: str
    start_marker: str
    end_marker: str
    ellipsis: str
    max_tokens: int


class SearchConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    page_size: int
    bm25_weights: Bm25Weights
    snippet: SnippetConfig
    rrf_k: int
    rrf_min_weight: float
    vector_candidates: int
    min_similarity: float


class CatalogConfig(BaseModel):
    """The conversion vocabulary, plus the three states the pipeline moves through.

    `conversion_states` is the whole set a row may be in; the other three name
    the members `index catalog extract` reads and writes. They are separate
    keys rather than positions in the list because a list is ordered by
    accident, and `conversion_states[1]` is not a contract anybody can see.
    """

    model_config = SECTION_MODEL_CONFIG
    conversion_states: list[str]
    initial_state: str
    converted_state: str
    failed_state: str

    @model_validator(mode="after")
    def _named_states_are_known(self) -> CatalogConfig:
        unknown = {
            key: value
            for key, value in (
                ("initial_state", self.initial_state),
                ("converted_state", self.converted_state),
                ("failed_state", self.failed_state),
            )
            if value not in self.conversion_states
        }
        if unknown:
            named = ", ".join(f"index.catalog.{key} {value!r}" for key, value in sorted(unknown.items()))
            raise ValueError(f"{named} is not one of index.catalog.conversion_states {self.conversion_states!r}")
        return self


class VectorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    provider: Literal["fastembed", "hash"]
    store: Literal["sqlite_vec", "memory"]
    model: str
    dimensions: int
    threads: int
    chunk_size: int
    chunk_overlap: int
    embed_batch_size: int
    write_batch_size: int

    @model_validator(mode="after")
    def _overlap_below_chunk_size(self) -> VectorConfig:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"index.vector.chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"index.vector.chunk_size ({self.chunk_size}); an overlap at or above the "
                f"chunk size never advances"
            )
        return self


class IndexConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    backend: Literal["sqlite", "memory"]
    sqlite: SqliteIndexConfig
    roots: list[IndexRoot]
    file_extensions: list[str]
    exclude: list[str]
    sync: IndexSyncConfig
    frontmatter: FrontmatterConfig
    observations: ObservationConfig
    relations: RelationConfig
    search: SearchConfig
    catalog: CatalogConfig
    vector: VectorConfig

    @model_validator(mode="after")
    def _roots_are_named_and_unique(self) -> IndexConfig:
        if not self.roots:
            raise ValueError("index.roots must list at least one root; the library indexes what it is told to")
        names = [root.name for root in self.roots]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"index.roots names must be unique -- duplicated: {duplicates}. "
                f"Entity keys are '{{root name}}/{{relative path}}', so a repeated name "
                f"collides two different files onto one key."
            )
        return self
