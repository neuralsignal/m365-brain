# m365-extract Maturity Assessment

_2026-03-17 — Phase 3 complete_

## Current State

25 source modules, 6 extractors, 2 storage backends, 149 tests, Bicep IaC, Docker, Azurite dev workflow. Phases 1-3 of the roadmap are implemented.

### What's Implemented

| Area | Status | Detail |
|------|--------|--------|
| Core library | Done | Graph client, auth (device code), state, config, markdown writer |
| Extractors | 6/8 | email, calendar, teams_chats, teams_channels, onedrive, sharepoint |
| Storage | Done | Local + Azure Blob backends, factory dispatch, StorageBackend protocol |
| Document conversion | Done | obsidian-import pass-through config, deferred import |
| CLI | Done | `auth login`, `sync --once`, `sync --continuous`, `--extractors` filter |
| Config | Done | Frozen dataclasses, env var expansion, Optional field support |
| Azure IaC | Done | Bicep templates (main.bicep), dev/prod parameter files |
| Dev workflow | Done | Azurite + Docker Compose, `.env.dev`, pixi tasks |
| Tests | 149 pass | Unit + mock + Azurite integration (9 skip when no Azurite) |
| Dev scripts | 3 | dev-setup.sh, deploy-infra.sh, teardown-dev.sh |

### What's Missing (Roadmap Phases 4-6)

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 4 | Multi-user web service (FastAPI, auth code flow, token store, scheduler) | Not started |
| Phase 5 | Webhooks + Azure App Service deployment + CI/CD | Not started |
| Phase 6 | Contacts + directory extractors | Not started |
| Future | MCP server integration | Not started |

## Gap Analysis vs. Reference Repos

Compared against obsidian-import (18 GH Actions workflows, MkDocs, PyPI, CHANGELOG) and excel-model (15 workflows, 191 tests, MkDocs, PyPI).

### Critical Gaps

| Dimension | m365-extract | Reference repos | Priority |
|-----------|-------------|-----------------|----------|
| GitHub Actions CI | None | ci.yml (lint + test on push/PR) | **P0** |
| Linting (ruff) | None | ruff (E,F,W,I,UP,B,SIM) in pyproject.toml | **P0** |
| Pre-commit hooks | None | ruff + ruff-format + pixi-lock | **P0** |
| README.md | None | 200+ lines (install, quickstart, architecture) | **P0** |
| CLAUDE.md | None | 10+ KB (engineering standards, build/test, agent context) | **P0** |
| CHANGELOG.md | None | Semantic versioning entries per release | **P1** |

### Medium Gaps

| Dimension | m365-extract | Reference repos | Priority |
|-----------|-------------|-----------------|----------|
| pixi tasks (lint/fmt) | 2 (test, test-azurite) | 7+ (lint, format, format-check, test-cov, docs-build, docs-serve) | **P1** |
| MkDocs docs site | None | Material theme, API ref via mkdocstrings, GitHub Pages deploy | **P1** |
| Coverage tracking | None | pytest-cov, 80% threshold | **P1** |
| PyPI publish workflow | None | OIDC auto-publish on tag | **P2** |
| Auto-tag workflow | None | Reads version from pyproject.toml, creates git tag | **P2** |

### Low Gaps (Automation Polish)

| Dimension | m365-extract | Reference repos | Priority |
|-----------|-------------|-----------------|----------|
| Agent workflows | None | 8 Claude agent workflows (code-quality, coverage, security, dep-audit, docs-freshness, issue-triage, PR-review, PR-autofix) | **P3** |
| Property-based tests | hypothesis in deps, unused | hypothesis used on pure functions | **P3** |
| py.typed marker | None | None (gap in reference repos too) | **P3** |
| mypy config | None | None (gap in reference repos too) | **P3** |

### Where m365-extract is Ahead

| Dimension | m365-extract | Reference repos |
|-----------|-------------|-----------------|
| Docker | Dockerfile + docker-compose.dev.yaml | None |
| IaC (Bicep) | Storage Account + params per env | None |
| Dev scripts | dev-setup.sh, deploy-infra.sh, teardown-dev.sh | None |
| Multi-backend storage | Local + Azure Blob + factory | Single backend |

## Architecture Quality

The codebase follows the workspace engineering principles well:

- **No defaults**: All config from YAML, crashes on missing values
- **Fail fast**: `SystemExit(1)` with clear messages on config errors
- **Modular**: Each extractor is a standalone module; storage backends are pluggable via protocol
- **Frozen dataclasses**: Config immutability enforced at the type level
- **Deferred imports**: Optional deps (azure-storage-blob, obsidian-import) imported at call time
- **Token provider abstraction**: GraphClient decoupled from auth flow
- **Delta sync**: Incremental sync via Graph API delta tokens, state persisted in JSON

## Implementation Plan

### Tier 1 — Foundational Hygiene

1. **ruff config** in pyproject.toml (E,F,W,I,UP,B,SIM; line-length 120; target py312)
2. **Pre-commit config** (ruff + ruff-format + pixi-lock)
3. **pixi tasks** (lint, format, format-check, test-cov)
4. **CI workflow** (ci.yml: lint + test on push/PR via setup-pixi)
5. **README.md** (install, quickstart, config, architecture, dev workflow)
6. **CLAUDE.md** (engineering context for agents)
7. **CHANGELOG.md** (Phase 1-3 entries)

### Tier 2 — Professional Packaging

8. **MkDocs** site with Material theme + mkdocstrings API reference
9. **Docs deploy workflow** (GitHub Pages on push to main)
10. **Coverage threshold** (80% via pytest-cov in CI)
11. **Auto-tag workflow** (version from pyproject.toml)
12. **Publish workflow** (PyPI via OIDC on tag)

### Tier 3 — Automation

13. **Claude agent workflows** (code-quality, test-coverage, security-scan, dep-audit)
14. **PR auto-review + auto-fix** (claude-code-action)
15. **Docs freshness check** (weekly)
