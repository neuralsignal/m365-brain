---
title: "Design: Connector Protocol"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# Connector Protocol

> **Read against ADR 0012.** This document specifies a plugin protocol for *file discovery* —
> "here are some bytes that exist, and where they came from". It is not a `SourceBackend`
> abstraction: `m365_brain` deliberately ships no source registry and no plugin slot waiting for a
> second SaaS integration. The Microsoft 365 extractors are a namespace, not a connector plugin.

## Context

The consuming workspace discovers files from multiple sources: local filesystem, OneDrive, SharePoint, and email attachments. This document generalizes that into a connector protocol — a pluggable interface for file discovery that works with any source.

The connector's job is discovery and metadata, not extraction. The lifecycle is: **discover → catalog → convert → index**. Connectors handle the first step; extraction (step 3) and indexing (step 4) are handled by the extraction pipeline and index sync respectively.

**Connectors vs storage:** Connectors discover files to import (PDF, DOCX, etc. from external sources). Storage (see `02-package-architecture.md`) is where the markdown files live (local filesystem or cloud). These are orthogonal concerns: a connector discovers a PDF on OneDrive, extraction converts it to markdown, and storage writes the markdown to wherever the configured vault root points.

## Scope for v0.1

**In scope:**

- `Connector` protocol definition
- `DiscoveredFile` dataclass
- Connector lifecycle: discover → catalog → convert → index
- Built-in: local filesystem connector
- Plugin registration via entry points
- State management: watermarks, checksums for incremental sync
- Configuration per connector in the config file

**Out of scope:**

- Cloud connectors (Google Drive, Notion) — these would be separate plugin packages
- Real-time file watching (inotify/fsevents) — batch discovery only for v0.1
- Two-way sync (write back to source) — in `m365_brain` write-back is the outbox's job, not a connector's

## Design

### Connector Protocol

```python
from typing import Protocol, Iterator
from dataclasses import dataclass
from datetime import datetime

@dataclass
class DiscoveredFile:
    """A file discovered by a connector."""
    source: str              # connector name (e.g., "local", "onedrive")
    library: str | None      # collection within source (e.g., "Documents")
    original_path: str       # canonical path in the source system
    relative_path: str       # path relative to library root
    filename: str            # filename without extension
    extension: str           # extension including dot (e.g., ".pdf")
    size_bytes: int
    modified_at: datetime

class Connector(Protocol):
    """Protocol for file discovery connectors."""

    @property
    def name(self) -> str:
        """Unique connector name. Used as `source` in file_catalog."""
        ...

    def discover(self, config: dict) -> Iterator[DiscoveredFile]:
        """Yield files from the source.

        The config dict comes from the config file under connectors.<name>.
        Connectors must handle their own authentication and error handling.
        Yields DiscoveredFile instances for each discovered file.
        """
        ...
```

Source: `m365_brain/extractors/` (for interface patterns), the retired index library `__init__.py:750-795` (file_catalog CRUD)

### DiscoveredFile Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `source` | str | Connector name | `"local"` |
| `library` | str \| None | Collection/folder within source | `"Documents"` |
| `original_path` | str | Canonical unique path | `"/home/user/docs/paper.pdf"` |
| `relative_path` | str | Path relative to library root | `"research/paper.pdf"` |
| `filename` | str | Name without extension | `"paper"` |
| `extension` | str | Extension with dot | `".pdf"` |
| `size_bytes` | int | File size | `1048576` |
| `modified_at` | datetime | Last modification time | `2026-03-08T10:30:00Z` |

### Lifecycle

```mermaid
graph LR
    A[Discover] -->|DiscoveredFile| B[Catalog]
    B -->|file_catalog row| C[Convert]
    C -->|markdown text| D[Index]
    D -->|entity + observations| E[Search]
```

1. **Discover** — Connector yields `DiscoveredFile` instances
2. **Catalog** — Each file is upserted into `file_catalog` table (deduplication by `original_path`)
3. **Convert** — Extraction pipeline converts file to markdown (see `04-file-extraction.md`)
4. **Index** — Converted markdown is synced into entity/observation/relation tables (see `05-database-search.md`)

### Local Filesystem Connector

The built-in connector for local files:

```python
class LocalConnector:
    """Discovers files on the local filesystem."""

    @property
    def name(self) -> str:
        return "local"

    def discover(self, config: dict) -> Iterator[DiscoveredFile]:
        """Walk configured directories for supported file types."""
```

Configuration in the config file:

```yaml
connectors:
  local:
    enabled: true
    watch_dirs:                     # directories to scan (relative to workspace root)
      - documents
      - research
    extensions:                     # file extensions to discover
      - .pdf
      - .docx
      - .pptx
      - .xlsx
    exclude_patterns:               # glob patterns to skip
      - "*.tmp"
      - ".git/**"
```

### Plugin Registration

External connectors register via `importlib.metadata` entry points:

```toml
# In a connector plugin's pyproject.toml
[project.entry-points."m365_brain.connectors"]
gdrive = "m365_brain_gdrive:GoogleDriveConnector"
```

Discovery at runtime:

```python
from importlib.metadata import entry_points

def load_connectors() -> dict[str, Connector]:
    """Load all registered connectors."""
    eps = entry_points(group="m365_brain.connectors")
    connectors = {}
    for ep in eps:
        cls = ep.load()
        instance = cls()
        connectors[instance.name] = instance
    return connectors
```

### State Management

Connectors need to track what they've already discovered for incremental sync:

1. **File-level deduplication** — `file_catalog.original_path` is UNIQUE. Upsert semantics: if the file exists and `modified_at` hasn't changed, skip.
2. **Watermark** — Connectors can store a watermark (e.g., last sync timestamp) to avoid re-scanning everything. Stored in the config-declared meta directory alongside the other sync state.
3. **Checksums** — For file content change detection, `file_catalog.modified_at` is the primary signal. Full checksums are computed only during extraction.

### Example Connector Walkthrough

Building a hypothetical S3 connector:

```python
# m365_brain_s3/connector.py
import boto3
from m365_brain.connectors import Connector, DiscoveredFile

class S3Connector:
    @property
    def name(self) -> str:
        return "s3"

    def discover(self, config: dict) -> Iterator[DiscoveredFile]:
        bucket = config["bucket"]
        prefix = config.get("prefix", "")
        s3 = boto3.client("s3")

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                filename = Path(key).stem
                extension = Path(key).suffix
                if not extension:
                    continue
                yield DiscoveredFile(
                    source="s3",
                    library=bucket,
                    original_path=f"s3://{bucket}/{key}",
                    relative_path=key[len(prefix):].lstrip("/"),
                    filename=filename,
                    extension=extension,
                    size_bytes=obj["Size"],
                    modified_at=obj["LastModified"],
                )
```

```toml
# pyproject.toml for the S3 connector plugin
[project.entry-points."m365_brain.connectors"]
s3 = "m365_brain_s3.connector:S3Connector"
```

```yaml
# the config file
connectors:
  s3:
    enabled: true
    bucket: my-knowledge-bucket
    prefix: documents/
```

## Decisions

### D-17: Protocol vs ABC for Connectors

- **Context:** How to define the connector interface.
- **Options:**
  - A) `typing.Protocol` — structural typing, no inheritance required
  - B) ABC (`abc.ABC`) — nominal typing, explicit inheritance
  - C) Simple duck typing — no formal interface
- **Chosen:** A) `typing.Protocol`
- **Consequences:** Connectors don't need to inherit from a base class. Any class with the right methods satisfies the protocol. This is more Pythonic and reduces coupling. Type checkers can verify protocol compliance. This matches the seam style already used for `StorageBackend` and `IndexBackend` — and carries the same obligation: a protocol ships an in-memory fake, or it is decoration.

### D-18: Connector Packaging Model

- **Context:** Should connectors be bundled in the main package or separate?
- **Options:**
  - A) All connectors bundled as extras
  - B) Only local connector bundled, cloud connectors as separate packages
  - C) All connectors as separate packages
- **Chosen:** B) Local connector bundled, cloud connectors separate
- **Consequences:** The core includes only the local filesystem connector (zero extra dependencies). Cloud connectors would be separate pip packages that register via entry points. This keeps the core lightweight and avoids pulling in cloud SDKs. Note that the Microsoft 365 extractors are *not* an instance of this pattern — they ship in the package as a namespace, by decision, not as a connector plugin (ADR 0012).

### D-19: Cloud Connector Authentication

- **Context:** How do cloud connectors authenticate?
- **Options:**
  - A) Environment variables only
  - B) Credential files (JSON/YAML)
  - C) OAuth browser flow
  - D) Connector-specific, documented in each plugin
- **Chosen:** D) Connector-specific
- **Consequences:** Each connector plugin documents its own authentication method. Common patterns: environment variables for tokens, JSON credential files for service accounts, OAuth flow for user-interactive auth. The `Connector.discover()` method receives the connector's config from the config file, which can include auth-related settings. The knowledge half does not handle authentication.

## Resolved Questions

1. **File content access** — **Resolved:** Connectors provide discovery only. File content access for cloud files is handled by the storage layer. Connectors yield `DiscoveredFile` with `original_path`; the extraction pipeline reads the file via storage.

## Open Questions

1. **Connector health checks** — Should there be a `ping()` method to verify connectivity before running discovery?
2. **Rate limiting** — Should the connector protocol include rate limiting guidance for cloud APIs?
3. **File deletion tracking** — When a file is deleted from the source, should the connector report it? Currently relies on comparing catalog against discovery results. Note the Microsoft 365 half solved this differently: one canonical upstream-removal handler that every extractor routes through.

## References

- File catalog CRUD: the retired index library, `__init__.py:750-910`
- Microsoft 365 extractors (interface patterns): `m365_brain/extractors/`
- Extract CLI: the consuming workspace's file skill, `scripts/extract.py`
- Entry points spec: <https://packaging.python.org/en/latest/specifications/entry-points/>
- ADR 0012 — no multi-source abstraction
