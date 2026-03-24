# m365-extract Maturity Assessment

_2026-03-24 — v0.2.2+ (Phases 1-4 + 6 complete, Phase 5 partial)_

## Current State

35 source modules, 8 extractors, 2 storage backends, 334 tests (25 test files), 9 Azurite integration tests, 18 GitHub Actions workflows, MkDocs site, pytest-cov at 94%. Phases 1-4 and 6 of the roadmap are implemented.

### What's Implemented

| Area | Status | Detail |
|------|--------|--------|
| Core library | Done | Graph client (friendly errors, hint system), auth (device code), state, config package, markdown writer |
| Sync API | Done | Public `sync.py` module — CLI and web both import from here |
| Extractors | 8/8 | email, calendar, teams_chats, teams_channels, onedrive, sharepoint, contacts, directory |
| Storage | Done | Local + Azure Blob backends, factory dispatch, StorageBackend protocol |
| Document conversion | Done | obsidian-import pass-through config, deferred import |
| CLI | Done | `auth login`, `auth status`, `sync --once`, `sync --continuous`, `sync --dry-run`, `--extractors` filter |
| Web service | Done | FastAPI app, auth code flow, token store, scheduler, per-user storage isolation, access control middleware |
| Config | Done | Config package (loader.py + schema.py), frozen dataclasses, env var expansion |
| Azure IaC | Done | Bicep templates (main.bicep), dev/prod parameter files |
| Dev workflow | Done | Azurite + Docker Compose, `.env.dev`, `.env.example`, pixi tasks |
| Tests | 334 pass | Unit + mock + Azurite integration (9 skip when no Azurite) |
| CI/CD | Done | 18 GitHub Actions workflows (CI, release-please, dark factory loop) |
| Linting | Done | ruff (E,F,W,I,UP,B,SIM), pre-commit hooks |
| Docs site | Done | MkDocs + Material theme, GitHub Pages deploy |
| Coverage | Done | pytest-cov, 94% (threshold: 80%) |
| Packaging | Done | PyPI publish workflow (OIDC on tag), release-please auto-versioning |
| Dev scripts | 3 | dev-setup.sh, deploy-infra.sh, teardown-dev.sh |

### What's Missing (Roadmap Phase 5)

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 5 | Graph webhooks (change notifications) | Not started — requires public HTTPS endpoint |
| Phase 5 | Azure App Service deployment | Not started |
| Future | MCP server integration | Not started |

### Entra App Registration Permissions

The app (`workflow-read`) requires delegated Graph API permissions. Some require admin consent. Only request scopes for extractors you intend to enable — requesting an ungranted scope blocks the entire login flow.

| Scope | Extractor | Admin consent? | Notes |
|-------|-----------|----------------|-------|
| `User.Read` | (all) | No | Token validation |
| `Mail.Read` | email | No | |
| `Calendars.Read` | calendar | No | |
| `Chat.Read` | teams_chats | No | |
| `ChannelMessage.Read.All` | teams_channels | Yes | |
| `Files.Read.All` | onedrive | Yes | |
| `Sites.Read.All` | sharepoint | Yes | |
| `Contacts.Read` | contacts | No | |
| `User.Read.All` | directory | Yes | Read user profiles |
| `Directory.Read.All` | directory | Yes | Manager chain + direct reports traversal |
| `offline_access` | (all) | No | Persistent token refresh |

## Architecture Quality

The codebase follows the workspace engineering principles well:

- **No defaults**: All config from YAML, crashes on missing values
- **Fail fast**: `SystemExit(1)` with clear messages on config errors
- **Modular**: Each extractor is a standalone module; storage backends are pluggable via protocol
- **Frozen dataclasses**: Config immutability enforced at the type level
- **Config package split**: `config.py` refactored into `config/` package (loader.py + schema.py) to stay under the 300-line limit
- **Shared helpers**: `_message_helpers.py` extracted for Teams extractors, `_execute_with_retry` extracted for retry logic in graph_client.py
- **Deferred imports**: Optional deps (azure-storage-blob, obsidian-import) imported at call time
- **Token provider abstraction**: GraphClient decoupled from auth flow
- **Delta sync**: Incremental sync via Graph API delta tokens, state persisted in JSON
- **Custom exceptions**: `GraphApiError` used instead of bare `RuntimeError`
- **Path traversal protection**: LocalBackend validates paths to prevent directory escape
- **Restrictive permissions**: MSAL token cache set to 0600

## Where m365-extract is Ahead of Reference Repos

| Dimension | m365-extract | Reference repos |
|-----------|-------------|-----------------|
| Docker | Dockerfile + docker-compose.dev.yaml | None |
| IaC (Bicep) | Storage Account + params per env | None |
| Dev scripts | dev-setup.sh, deploy-infra.sh, teardown-dev.sh | None |
| Multi-backend storage | Local + Azure Blob + factory | Single backend |
| Dark factory automation | Full autonomous agent loop (8 scan/fix workflows) | None |

## Dark Factory Activity

Between 2026-03-17 and 2026-03-23, autonomous Claude agents made 50+ commits across 4 releases (0.1.0 → 0.2.0 → 0.2.1 → 0.2.2):

### Infrastructure Built

- 18 GitHub Actions workflows: CI, release-please, docs-deploy, publish, and 8 dark factory agent workflows (code-quality, test-coverage, security-scan, dep-audit, docs-freshness, issue-triage, PR-review, PR-autofix)
- ruff linting + pre-commit hooks
- MkDocs + Material docs site with GitHub Pages deploy
- CLAUDE.md, README.md, CHANGELOG.md, LICENSE
- pytest-cov with 82%+ coverage threshold
- pixi tasks: lint, format, format-check, test, test-cov, test-azurite, docs-build, docs-serve

### Code Quality Improvements

- `config.py` split into `config/` package (loader.py + schema.py) to meet 300-line limit (#10)
- Shared `_message_helpers.py` extracted for Teams extractors (#12)
- Shared `_execute_with_retry` extracted for retry logic (#14)
- Dead `write_markdown` function removed (#13)
- Type hints added to `_run_extractors` and `_run_continuous` (#11)
- Bare `RuntimeError` replaced with `GraphApiError` (#17)
- Silent exception swallowing fixed in teams_chats.py (#15)

### Security Fixes

- Path traversal protection added to LocalBackend (#3)
- Restrictive permissions (0600) on MSAL token cache (#4)
- Cryptography pin widened for CVE-2026-26007 (#35)

### Test Coverage Expansion

- CLI tests (#28), token_provider tests (#27), base extractor tests (#29), message helpers tests (#30)
- Test count: 158 → 247 (56% increase)
- Test files: 18 → 23

### New Extractors

- Contacts extractor (Phase 6)
- Directory extractor (Phase 6)
