---
title: "Design: Database & Search"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# Database & Search

## Context

The knowledge system uses SQLite as a derived index over markdown files. Files are the source of truth; the database is rebuilt from them. The database provides full-text search (FTS5), structured queries, graph traversal, and optional vector similarity search. This document specifies the schema, search interfaces, sync strategy, and migration approach.

In `m365_brain` this is the `index/` subpackage and the `IndexBackend` protocol, whose first implementation is SQLite + FTS5.

## Scope for v0.1

**In scope:**

- SQLite schema (entity, observation, relation, file_catalog, FTS5, vector tables)
- FTS5 configuration and query syntax
- Vector search via sqlite-vec + fastembed (optional extra)
- Hybrid search: RRF fusion of FTS5 + vector
- Incremental sync: markdown files to database
- Connection management: WAL, busy_timeout, readonly mode
- Schema migration strategy

**Out of scope:**

- External vector databases (Pinecone, Weaviate, etc.)
- Multi-user concurrent access (single-user, single-writer model)
- Replication or backup strategies

## Design

### SQLite Schema

#### Entity Table

The core table. One row per markdown file.

```sql
CREATE TABLE entity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    permalink TEXT UNIQUE NOT NULL,
    file_path TEXT UNIQUE NOT NULL,
    tags TEXT,                          -- JSON array of strings
    metadata TEXT,                      -- JSON object of non-structural frontmatter
    checksum TEXT NOT NULL,             -- SHA-256 of file content
    created_at TEXT NOT NULL,           -- ISO 8601
    updated_at TEXT NOT NULL            -- ISO 8601
);

CREATE INDEX idx_entity_type ON entity(type);
CREATE INDEX idx_entity_updated ON entity(updated_at);
```

Source: the retired index library, `__init__.py:48-60`

#### Observation Table

```sql
CREATE TABLE observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,                          -- JSON array or NULL
    context TEXT                        -- parenthesized context or NULL
);

CREATE INDEX idx_observation_entity ON observation(entity_id);
CREATE INDEX idx_observation_category ON observation(category);
```

Source: the retired index library, `__init__.py:62-69`

#### Relation Table

```sql
CREATE TABLE relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    to_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
    to_name TEXT NOT NULL,              -- target title (for unresolved refs)
    relation_type TEXT NOT NULL,
    context TEXT,
    UNIQUE(from_entity_id, to_name, relation_type)
);

CREATE INDEX idx_relation_from ON relation(from_entity_id);
CREATE INDEX idx_relation_to ON relation(to_entity_id);
CREATE INDEX idx_relation_type ON relation(relation_type);
```

`to_entity_id` is NULL for forward references (target entity doesn't exist yet). `to_name` is always populated for display.

Source: the retired index library, `__init__.py:71-88`

#### FTS5 Search Index

```sql
CREATE VIRTUAL TABLE search_index USING fts5(
    title,
    content,
    tags,
    type UNINDEXED,
    file_path UNINDEXED,
    permalink UNINDEXED,
    tokenize='unicode61'
);
```

The `unicode61` tokenizer provides Unicode-aware tokenization. `UNINDEXED` columns are stored but not searchable — used for result metadata.

Source: the retired index library, `__init__.py:90-100`

#### File Catalog Table

Tracks discovered files from connectors, their conversion status, and metadata.

```sql
CREATE TABLE file_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,               -- connector name
    library TEXT,                       -- library/collection within source
    original_path TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL,
    knowledge_path TEXT,                -- path to converted .md file
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    conversion_status TEXT NOT NULL DEFAULT 'pending',
    converted_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_fc_source ON file_catalog(source);
CREATE INDEX idx_fc_ext ON file_catalog(extension);
CREATE INDEX idx_fc_status ON file_catalog(conversion_status);
CREATE INDEX idx_fc_filename ON file_catalog(filename);
CREATE INDEX idx_fc_modified ON file_catalog(modified_at DESC);
```

Conversion status values: `pending`, `eager`, `converted`, `failed`, `skipped`.

Source: the retired index library, `__init__.py:102-125`

#### Vector Tables (optional, requires the `[vector]` extra)

```sql
CREATE TABLE search_vector_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,            -- "chunk_0", "chunk_1", etc.
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,          -- SHA-256 of chunk text
    updated_at TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entity(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uix_vector_chunks_entity_key
    ON search_vector_chunks (entity_id, chunk_key);

-- sqlite-vec virtual table
CREATE VIRTUAL TABLE search_vector_embeddings
    USING vec0(embedding float[384]);   -- dimensions from config
```

The `search_vector_embeddings.rowid` maps to `search_vector_chunks.id` for joining.

Source: the retired index library, `__init__.py:127-141`

The dimension count is a config value, not a literal — it was a module constant in the retired library, and `INTENT.md` names exactly that class of constant as the reason that library was unusable by anyone else.

### Connection Management

```python
@contextmanager
def connect(config: IndexConfig, readonly: bool) -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection."""
```

Settings:

- **Journal mode:** WAL for write connections (better concurrency)
- **Readonly mode:** `PRAGMA query_only=ON` for read connections
- **Foreign keys:** Always enabled
- **Busy timeout:** From config (`busy_timeout_ms`)
- **Row factory:** `sqlite3.Row` for dict-like access

Connection lifecycle:

1. Open connection with URI
2. Set pragmas (journal mode, foreign keys, busy timeout, query_only)
3. Yield to caller
4. Commit on success (write), rollback on exception (write)
5. Close

Source: the retired index library, `__init__.py:187-210`

### Storage-Aware Paths

The database path (`database_path` in the config file) is resolved via the storage layer. For local workspaces, this is a filesystem path relative to the config file. For cloud-backed workspaces, the database is always local (SQLite requires local filesystem), while the indexed files are accessed through the `StorageBackend` protocol (see `02-package-architecture.md`).

The sync process reads files through the storage abstraction:

1. `storage.list_files(root, "*.md")` — list files (local or cloud)
2. `storage.read_file(file_path)` — read file content
3. Parse locally (frontmatter, observations, relations)
4. Write to local SQLite database

### FTS5 Query Syntax

User queries are normalized to FTS5 syntax:

| User Input | FTS5 Query | Behavior |
|------------|------------|----------|
| `python async` | `python* AND async*` | Prefix match, implicit AND |
| `"exact phrase"` | `"exact phrase"` | Exact phrase match |
| `python NOT java` | `python* NOT java*` | Boolean NOT |
| `python OR rust` | `python* OR rust*` | Boolean OR |
| `-deprecated` | `NOT deprecated*` | Prefix negation |

All non-operator, non-quoted tokens get `*` suffix for prefix matching.

Source: the consuming workspace's knowledge-search script, `search.py:36-93`

### Vector Search

When the `[vector]` extra is installed:

1. **Chunking:** Entity content is split into chunks (configurable `chunk_size` and `chunk_overlap`). Split on markdown headers first, then line-by-line, then merge short chunks.
2. **Embedding:** Chunks are embedded via an `EmbeddingProvider` — fastembed first, with the model name and dimension count coming from config (`BAAI/bge-small-en-v1.5`, 384 dimensions in the reference config).
3. **Storage:** Embeddings stored in `search_vector_embeddings` (sqlite-vec virtual table), chunk text in `search_vector_chunks`.
4. **Query:** Query text is embedded with the same model, then matched via `embedding MATCH` (cosine distance in sqlite-vec).
5. **Similarity:** `1.0 / (1.0 + max(distance, 0.0))` — ranges from 0 to 1, configurable minimum threshold.

Source: the retired index library, `__init__.py:918-1031`

### Hybrid Search (RRF Fusion)

Combines FTS5 and vector results using Reciprocal Rank Fusion:

1. Run FTS5 search → ranked list with BM25 scores
2. Run vector search → ranked list with cosine distances
3. For each result, compute: `score = Σ (weight / (K + rank))` across both lists
   - K = 60 (RRF constant)
   - FTS weight = normalized BM25 score (min 0.1)
   - Vector weight = similarity score (min 0.1)
4. Sort by fused score descending
5. Deduplicate by entity ID (same entity may appear in both lists)

Source: the consuming workspace's knowledge-search script, `search.py:320-441`

### Incremental Sync

The sync process keeps the database in sync with markdown files:

1. **Scan** the configured index roots for `.md` files (via the storage layer — supports local paths and cloud URIs)
2. **Checksum** each file (SHA-256)
3. **Compare** with stored checksums in `entity.checksum`
4. **Skip** files with matching checksums (unchanged)
5. **Parse** changed/new files (frontmatter, observations, relations)
6. **Upsert** entity, observations, relations into database
7. **Update** FTS5 search index
8. **Remove** database entries for deleted files
9. **Resolve** forward references (match `to_name` to existing entities)

For vector sync (when `[vector]` is enabled):

1. Read all entities from the FTS index
2. Compare chunk hashes with stored `source_hash`
3. Embed only new/changed chunks
4. Write in batches (configurable `batch_size`)
5. Prune stale chunks for deleted entities

Source: the retired index library, `__init__.py:1042-1197`

### Graph Traversal

BFS traversal from a seed entity:

```python
def traverse(conn, seed_id: int, max_depth: int) -> list[dict]:
```

At each depth level:

1. Find all outgoing relations from frontier entities
2. Find all incoming relations to frontier entities
3. Add newly discovered entities to next frontier
4. Track visited entities to prevent cycles

Returns: `[{depth, relation_type, direction, from_entity_id, target, context}]`

Source: the retired index library, `__init__.py:498-576`

## Decisions

### D-13: sqlite-vec as Optional Extra

- **Context:** sqlite-vec requires a native extension (.so/.dylib). Not all platforms have pre-built wheels.
- **Options:**
  - A) Required dependency — everyone gets vector search
  - B) Optional extra `[vector]` — installed only when needed
  - C) A separate `m365-brain-vector` distribution
- **Chosen:** B) Optional extra
- **Consequences:** The knowledge half has zero native dependencies (pure Python + stdlib sqlite3). Vector search is opt-in. Code that imports `index.vector` checks for sqlite-vec availability and raises a clear error if missing.

### D-14: Embedding Model Configurability

- **Context:** The retired library hardcoded `BAAI/bge-small-en-v1.5`. Users may want different models.
- **Options:**
  - A) Hardcoded model — simple, consistent
  - B) Configurable in the config file — user chooses model
  - C) Pluggable embedding backend (fastembed, sentence-transformers, OpenAI)
- **Chosen:** B) Configurable, with C) as the protocol shape
- **Consequences:** `index.vector.model` and `index.vector.dimensions` in config. Changing the model requires a full re-embed (`index sync --rebuild-vectors`). Only fastembed is supported in v0.1; other providers arrive as `EmbeddingProvider` implementations, which `CONTRACTS.md` already lists as a seam.

### D-15: External Vector Stores or SQLite-Only

- **Context:** Should we support Pinecone, Weaviate, Qdrant, etc.?
- **Options:**
  - A) SQLite-only (sqlite-vec)
  - B) Pluggable vector backend
  - C) Multiple backends in core
- **Chosen:** A) SQLite-only for v0.1, behind a `VectorStore` protocol
- **Consequences:** Keeps the system simple, local-first, zero-infrastructure. sqlite-vec is sufficient for knowledge bases up to ~100K documents. If users need external vector stores, the protocol is the place to add one — and per the "every protocol ships an in-memory fake" principle, the seam is exercised rather than decorative.

### D-16: Schema Migration Approach

- **Context:** Currently uses `CREATE TABLE IF NOT EXISTS` with no versioning. Future versions may need schema changes.
- **Options:**
  - A) `CREATE TABLE IF NOT EXISTS` only — works for additive changes
  - B) Version number in a `schema_version` table + migration scripts
  - C) Use Alembic or similar migration framework
  - D) Rebuild from files (since the DB is derived from markdown files)
- **Chosen:** D) Rebuild from files, with B) as safety net
- **Consequences:** Since the database is a derived index, the safest migration is always a full rebuild. For non-breaking changes (new columns, new indexes), use `CREATE ... IF NOT EXISTS`. For breaking changes (column renames, type changes), document in release notes that a rebuild is required. A `schema_version` table tracks the current schema version so the code can detect incompatible databases and prompt for rebuild. This is why the index is declared derived and disposable in `INTENT.md`; note that the *admin* database is a different thing and does use Alembic.

## Resolved Questions

1. **Concurrent access** — **Resolved:** WAL mode provides multiple readers + single writer. No connection pooling in v0.1; single-connection model per process.
2. **Database location** — **Resolved:** Configurable via `database_path`. Always local (SQLite requirement), even when the indexed files are on cloud storage.

## Open Questions

1. **FTS5 ranking** — BM25 is the default FTS5 ranking function. Should we expose ranking customization?
2. **Checksum algorithm** — SHA-256 is fine but slow for large files. Consider xxhash for file catalog entries.

## References

- Schema DDL: the retired index library, `__init__.py:48-141`
- Connection management: `__init__.py:187-228`
- Vector search: `__init__.py:918-1031`
- Sync logic: `__init__.py:1042-1197`
- Graph traversal: `__init__.py:498-576`
- Search CLI: the consuming workspace's knowledge-search script
- sqlite-vec: <https://github.com/asg017/sqlite-vec>
- fastembed: <https://github.com/qdrant/fastembed>
- FTS5 docs: <https://www.sqlite.org/fts5.html>
