---
title: "Design: Extraction Integration"
status: draft
date: 2026-03-09
author: Matthias Christenson
---

# Extraction Integration

## Context

File extraction (PDF, DOCX, PPTX, XLSX → markdown) was originally designed as an internal `extraction/` subpackage of the knowledge layer. During decomposition review, extraction was identified as having high standalone reuse value — RAG pipelines, search indexing, document processing, and other use cases outside knowledge management all need the same capability.

Extraction now lives in **obsidian-import** (`pip install obsidian-import`), a separate MIT-licensed package under the neuralsignal GitHub org. The knowledge layer integrates with it via an optional extra — in `m365_brain` that extra is `[convert]`, which is present today and already declares `obsidian-import[markitdown,docling]`.

## Scope for v0.1

**In scope:**

- How the package delegates to `obsidian-import`
- The `extract` CLI command and its delegation pattern
- Integration with the file catalog and sync pipeline
- Configuration bridge between the package config file and `obsidian-import`

**Out of scope:**

- obsidian-import's internal architecture (see obsidian-import's own design docs)
- Per-format extractor details (owned by obsidian-import)

## Design

### Package Ecosystem

```
agentic-engineering-kit (templates only -- NOT a pip dependency)
    │
    │ provides: dark factory workflows, CONSTITUTION, rules, knowledge-skill templates
    │ (installed/copied into each repo, not imported at runtime)
    │
m365-brain (own GitHub repo + dark factory)
    │
    ├── knowledge half: config, model, parsers, index, connectors, storage, validation, CLI
    ├── Microsoft 365 half: extractors, vault, outbox, Graph transport
    ├── [convert] ──depends on──> obsidian-import (MIT, separate repo)
    ├── [azure]: Azure Blob storage backend
    ├── [vector] (pending): fastembed + sqlite-vec
    ├── [export] (pending) ──depends on──> obsidian-export (MIT, separate repo)
    │
    │ plugin entry points:
    ├── m365_brain.connectors (for file-discovery connector plugins)
    └── m365_brain.extractors (for custom format plugins)

obsidian-import (MIT, standalone repo + dark factory)
    │
    ├── Config-driven CLI (Click + YAML): per-file-type backend selection
    ├── File discovery via glob patterns + directory walking
    ├── Extraction backends: native (pdfplumber, defusedxml, etc.), markitdown, docling
    ├── Output: Obsidian-flavored markdown with YAML frontmatter + metadata
    ├── Extras: [docling], [markitdown]
    └── consumed via the [convert] extra

obsidian-export (MIT, ALREADY standalone with dark factory)
    │
    └── consumed via the [export] extra
```

Note the asymmetry against ADR 0012: an *extraction* plugin slot is fine, because a format backend is a pure function of bytes. A *source* plugin slot is not — `m365_brain` deliberately has no `SourceBackend` registry.

### obsidian-import Package Summary

**PyPI**: `obsidian-import` | **Import**: `obsidian_import` | **License**: MIT | **Org**: neuralsignal

Mirrors obsidian-export's patterns:

- `pixi.toml` + `pyproject.toml` (hatchling)
- Click CLI entry point: `obsidian-import convert`, `obsidian-import discover`, `obsidian-import doctor`
- YAML config for per-file-type backend selection and glob patterns
- TDD with pytest + hypothesis
- Dark factory CI/CD from agentic-engineering-kit templates

**Module layout**:

```
obsidian_import/
├── __init__.py          # Public API: extract_file(), discover_files()
├── cli.py               # Click CLI
├── config.py            # Pydantic config models
├── discovery.py         # Glob-based file discovery
├── registry.py          # Backend registry (per-file-type dispatch)
├── output.py            # Obsidian markdown formatter (frontmatter, headings, tables)
├── backends/
│   ├── __init__.py
│   ├── native_pdf.py    # pdfplumber + pypdf
│   ├── native_docx.py   # defusedxml
│   ├── native_pptx.py   # python-pptx
│   ├── native_xlsx.py   # openpyxl
│   ├── markitdown.py    # markitdown wrapper
│   └── docling.py       # docling wrapper
└── exceptions.py        # Custom exceptions
```

**Config example (obsidian-import.yaml)**:

```yaml
input:
  directories:
    - path: ./documents
      extensions: [".pdf", ".docx", ".pptx", ".xlsx"]
      exclude: ["*.tmp", "~$*"]
    - path: s3://my-bucket/reports
      extensions: [".pdf"]

output:
  directory: ./vault/extracted
  frontmatter: true
  metadata_fields:
    - title
    - source
    - original_path
    - file_type
    - extracted_at
    - page_count

backends:
  pdf: native          # "native" (pdfplumber+pypdf) | "docling" | "markitdown"
  docx: native         # "native" (defusedxml) | "docling" | "markitdown"
  pptx: native         # "native" (python-pptx) | "markitdown"
  xlsx: native         # "native" (openpyxl) | "markitdown"
  default: markitdown  # fallback for unknown types

extraction:
  timeout_seconds: 120
```

### Delegation Pattern: `extract`

The `extract` CLI command delegates to `obsidian-import`:

```python
# m365_brain/cli.py (extract command)

@cli.command()
@click.argument("path")
@click.option("--search", help="Search file catalog by name")
@click.option("--all-pending", is_flag=True, help="Extract all pending files")
@click.option("--limit", type=int, help="Limit batch extraction count")
def extract(path, search, all_pending, limit):
    """Extract files to markdown via obsidian-import."""
    try:
        from obsidian_import import extract_file
    except ImportError:
        raise click.ClickException(
            "obsidian-import is not installed. "
            "Install it with: pip install m365-brain[convert]"
        )

    # Extract the file
    result = extract_file(path)

    # Write into the configured vault root
    output_path = workspace.write_extracted(result)

    # Update file catalog
    workspace.catalog.mark_converted(path, output_path)

    # Sync the new file into the index
    workspace.sync(paths=[output_path])
```

Key design: `extract` is a thin wrapper that:

1. Calls `obsidian_import.extract_file()` for the actual conversion
2. Writes the result into the configured vault root (via the storage layer)
3. Updates the file catalog status
4. Triggers an incremental sync for the new file

### Integration with File Catalog

The file catalog (see `05-database-search.md`) tracks files through the discovery → extraction → indexing lifecycle. Integration points:

| Stage | Owner | Action |
|-------|-------|--------|
| Discover | package connectors | Populate `file_catalog` with `status: pending` |
| Extract | obsidian-import (via `extract`) | Convert file to markdown |
| Catalog update | package | Set `status: converted`, store `knowledge_path` |
| Index | package sync | Parse markdown, upsert entity/observations/relations |

### Custom Extractor Entry Points

Users can register custom extractors via entry points. These are checked before delegating to obsidian-import:

```toml
# In a custom extractor plugin's pyproject.toml
[project.entry-points."m365_brain.extractors"]
myformat = "my_package:MyFormatExtractor"
```

```python
from typing import Protocol
from pathlib import Path

class Extractor(Protocol):
    def extract(self, path: Path, timeout: int) -> str:
        """Convert a file to markdown text. Raises on failure."""
        ...
```

Resolution order:

1. Check `m365_brain.extractors` entry points for the file extension
2. Fall back to `obsidian-import` (if installed)
3. Raise error if no extractor found

## Decisions

### D-24: Extraction as Separate Package

- **Context:** Should file extraction live inside the package or in a separate one?
- **Options:**
  - A) Internal `extraction/` subpackage — simpler, one install
  - B) Separate `obsidian-import` package — reusable, independent release cycle, MIT license
  - C) Keep both — internal basic extractors + optional obsidian-import for advanced
- **Chosen:** B) Separate package
- **Consequences:** Extraction has broad reuse value beyond knowledge management (RAG, search, doc processing). A separate package allows an independent release cycle and a cleaner dependency boundary. The `[convert]` extra declares it as optional. The tradeoff is one more package to install for extraction workflows, but `pip install m365-brain[convert]` makes this seamless. This decision is already realised: `m365_brain/converters/` delegates to obsidian-import rather than carrying format code.

## Resolved Questions

The original extraction design doc had these open questions. They are now owned by obsidian-import:

1. **Timeout mechanism** — Owned by obsidian-import. Will use threading timeout (cross-platform).
2. **Large file handling** — Owned by obsidian-import. Configurable `max_file_size` in obsidian-import.yaml.
3. **Encoding detection** — Owned by obsidian-import. UTF-8 required; chardet as optional fallback.
4. **Extraction quality metrics** — Deferred to obsidian-import v0.2.

## References

- Original converters: the consuming workspace's file skill, `scripts/converters/`
- obsidian-import: <https://github.com/neuralsignal/obsidian-import>
- obsidian-export: <https://github.com/neuralsignal/obsidian-export>
- markitdown: <https://github.com/microsoft/markitdown>
- docling: <https://github.com/DS4SD/docling>
