---
title: "Design: Vision & Scope"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# Vision & Scope

> **Where this landed.** These nine documents were written for a standalone knowledge-layer
> distribution. That distribution was never built as its own unit: the knowledge layer folds into
> `m365_brain` instead (ADR 0006), and these documents are its blueprint. Read every
> "the package" below as the knowledge half of `m365_brain` — `model.py`, `parsers/`, `index/`,
> and the `workspace.py` facade.

## Context

The consuming workspace contained a mature, production-grade knowledge management system: markdown files with structured observations and relations, SQLite+FTS5+vector search, file extraction from PDF/DOCX/PPTX/XLSX, and an agentic workflow where AI assistants read, search, and update the knowledge base as part of their work.

That system was tightly coupled to one workspace. The goal is to extract the general-purpose core into a library anyone can use to bootstrap their own agentic knowledge layer — installable with pip, uv, or pixi, and carrying no trace of the workspace it came from.

The knowledge layer complements an agentic engineering kit, which provides rules, constitution, skills, and agent templates. The kit defines *how agents should work*; the knowledge layer provides *what agents can know and remember*.

## Scope for v0.1

### What the knowledge layer IS

- A Python library and CLI for managing a markdown-based knowledge base
- A SQLite+FTS5 search engine over structured markdown files
- A parser for the observation/relation markdown format
- Integration with obsidian-import for file extraction (PDF, DOCX, PPTX, XLSX to markdown)
- A connector protocol for pluggable file discovery
- Optional vector search via fastembed + sqlite-vec
- Bundled rules and skills for AI agent integration
- Cross-platform (Linux, macOS, Windows — no WSL requirement)

### What the knowledge layer is NOT

- Not a full PKM application (no GUI, no Obsidian replacement)
- Not a hosted service or SaaS product
- Not an M365/Outlook/Exchange integration — that half is the *other* half of `m365_brain`, and the layering rule in `scripts/check_structure.py` keeps `index/` from ever importing `m365/`
- Not dependent on any specific AI provider or model
- Not dependent on an agentic engineering kit (complementary, not coupled)
- Not a database replacement — SQLite is the only supported backend

### Target Users

1. **Agentic-engineering-kit users** who want to add persistent knowledge to their AI workflows
2. **PKM practitioners** who want programmatic access to their markdown knowledge bases
3. **AI assistant builders** who need structured knowledge retrieval for their agents
4. **Solo developers and researchers** who want a local-first, file-based knowledge system

### MVP Success Criteria

- `pip install` works on Python 3.11+
- `init` scaffolds a working knowledge workspace
- `index sync` indexes markdown files into SQLite+FTS5
- `index search "query"` returns ranked results
- `extract <file>` delegates to obsidian-import and indexes the result
- The knowledge model spec is documented and parseable by third-party tools
- At least one bundled agent skill demonstrates the search-first workflow
- All public APIs are typed and documented

## Decisions

### D-01: Package Name and PyPI Namespace

- **Context:** Need a clear, memorable name that communicates purpose.
- **Options:**
  - A) A standalone knowledge-layer package, evocative, pairing with an agentic engineering kit
  - B) A descriptive but generic name
  - C) A short name, but conflicts exist
- **Chosen:** A) a standalone, evocatively-named package
- **Consequences:** Clear pairing with the kit. **Superseded by ADR 0006 and ADR 0009:** no standalone distribution ships. The layer lives inside `m365_brain` (PyPI `m365-brain`, import `m365_brain`), and the name says both halves out loud rather than hiding one behind a metaphor.

### D-02: Single Package with Extras vs Namespace Packages

- **Context:** The package has a core (parsers, DB, CLI) and optional capabilities (extraction, vector search, export). How to structure this?
- **Options:**
  - A) Single package with `[extras]` — one `pip install`, optional deps via extras
  - B) Namespace packages (a `.core` package, a `.extract` package, and so on) — separate PyPI packages
  - C) Monorepo with multiple packages sharing a namespace
- **Chosen:** A) Single package with extras
- **Consequences:** Simpler installation, simpler versioning, simpler CI. `obsidian-import` handles extraction with its own `[docling]` and `[markitdown]` extras. If the package grows very large, we can split later — YAGNI. In `m365_brain` the extras present today are `[azure]`, `[convert]` (obsidian-import), `[admin]`, `[all]`, and `[dev]`; `[vector]`, `[export]`, and `[cloud]` remain designed-but-pending.

### D-03: License Choice

- **Context:** Want to allow open-source and academic use but restrict commercial use without permission. Must be a recognized license, not a custom one.
- **Options:**
  - A) MIT / Apache 2.0 — fully permissive, no commercial restriction
  - B) BSL 1.1 (Business Source License) — free for non-production use, converts to open source (Apache 2.0) after a change date (e.g., 4 years). Used by MariaDB, Sentry, Cockroach, HashiCorp.
  - C) Elastic License 2.0 — free use including production, but no offering as a managed service. Simpler than BSL but narrower restriction.
  - D) SSPL — MongoDB's license. OSI does not consider it open source. Controversial.
  - E) CC BY-NC-SA 4.0 — designed for creative works, not software. Not recommended for code.
- **Chosen:** B) BSL 1.1
- **Consequences:**
  - Anyone can use, modify, and redistribute for non-production purposes (development, testing, academic, personal)
  - Production use requires a commercial license from the copyright holder
  - After the change date (4 years from each release), the code converts to Apache 2.0
  - Well-understood license with precedent (HashiCorp, Sentry, CockroachDB)
  - May reduce some open-source contributions, but protects commercial value
  - The change date ensures eventual full open-source availability
- **Superseded:** the absorbing package ships under MIT (`LICENSE`, `pyproject.toml`). The reasoning above is kept because it is the argument any future relicensing has to answer.

### D-04: Relationship to an Agentic Engineering Kit

- **Context:** How tightly should the knowledge layer integrate with such a kit?
- **Options:**
  - A) Hard dependency — the package requires the kit
  - B) Complementary, not dependent — works standalone, ships its own rules/skills
  - C) Kit extension — the package is a kit plugin
- **Chosen:** B) Complementary, not dependent
- **Consequences:** `init --setup-agents` can install rules/skills into a kit-structured project, but the package works without the kit. Users who don't use the kit still get full functionality via the CLI and Python API.

### D-22: Package Relationships

- **Context:** The consuming workspace had a knowledge-index library and a separate PDF-export package. How do these relate to the knowledge layer?
- **Options:**
  - A) The knowledge layer subsumes both
  - B) The knowledge layer subsumes the index library, PDF export stays independent
  - C) All three remain separate, the knowledge layer depends on the index library
- **Chosen:** B) subsume the index library; PDF export stays independent
- **Consequences:** The index library's code is absorbed and decomposed into proper modules — in `m365_brain` that is `model.py`, `parsers/`, `index/`, and the `workspace.py` facade (ADR 0006). The export package remains separate, listed as an optional dependency under the `[export]` extra. No circular dependencies. File extraction moves to `obsidian-import` (MIT, separate repo under the neuralsignal GitHub org); the `[convert]` extra declares it as an optional dependency. This keeps extraction reusable for RAG pipelines and other use cases outside knowledge management.

### D-23: Storage Abstraction (fsspec)

- **Context:** Knowledge files can live on local filesystem or cloud storage (S3, Azure ADLS, GCS). How should the package access them?
- **Options:**
  - A) Direct filesystem only — no abstraction, local paths only
  - B) fsspec abstraction layer — single API over local and cloud backends
  - C) Custom storage protocol — define an internal interface and implement adapters
- **Chosen:** B) fsspec
- **Consequences:** The configured index roots can be `./vault/`, `s3://bucket/vault/`, or `abfss://container@account.dfs.core.windows.net/vault/`. Cloud backends installed via the `[cloud]` extra. Local filesystem works with zero extra deps (fsspec ships with a Python-compatible local backend). **Note:** `m365_brain` chose C for its own `StorageBackend` protocol (local filesystem, Azure Blob) before the fold; the two are reconciled during the knowledge-layer stage.

## Pre-Release Action Items

1. **PyPI availability** — verify the distribution name is available on PyPI before release.

## References

- The retired index library — the monolith these documents decompose
- agentic-engineering-kit: <https://github.com/anthropics/agentic-engineering-kit>
- BSL 1.1 text: <https://mariadb.com/bsl11/>
- HashiCorp BSL adoption: <https://www.hashicorp.com/bsl>
- obsidian-import: <https://github.com/neuralsignal/obsidian-import>
- obsidian-export: <https://github.com/neuralsignal/obsidian-export>
