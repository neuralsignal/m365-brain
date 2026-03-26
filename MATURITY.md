# m365-extract Maturity Assessment

_2026-03-26 — v0.2.2+ (Phases 1-6 complete, Phase 5A-5C done, 5E dev deployed, prod pending)_

## Current State

40+ source modules, 8 extractors, 2 storage backends, 398 tests (37 test files), 9 Azurite integration tests, 18 GitHub Actions workflows, MkDocs site, pytest-cov at 94%. Phases 1-6 of the roadmap are implemented. Phase 5A-5C done. Phase 5E dev environment deployed and working end-to-end (OAuth login, dashboard, daemon sync to blob). Prod deployment pending (Entra redirect URI + GitHub secrets + tag). Live validated against real Graph API (2026-03-24). Config refactored to composable fragments. Alembic DB migrations replace create_all(). Log Analytics observability added to Bicep.

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
| Config | Done | Composable YAML fragments in config/, deep-merge loader, frozen pydantic models, env var expansion |
| DB migrations | Done | Alembic via Reflex scaffolding, initial schema migration, replaces create_all() |
| Observability | Done | Log Analytics workspace, diagnostic settings for App Service + PostgreSQL |
| Azure IaC | Done | Bicep: Storage, ACR, PostgreSQL, App Service, ACI, Key Vault, Log Analytics |
| Dev workflow | Done | Azurite + Docker Compose, `.env.dev`, `.env.example`, pixi tasks |
| Tests | 398 pass | Unit + mock + Azurite integration (9 skip when no Azurite), 37 test files |
| CI/CD | Done | 18 GitHub Actions workflows (CI, release-please, dark factory loop) |
| Linting | Done | ruff (E,F,W,I,UP,B,SIM), pre-commit hooks |
| Docs site | Done | MkDocs + Material theme, GitHub Pages deploy |
| Coverage | Done | pytest-cov, 94% (threshold: 80%) |
| Packaging | Done | PyPI publish workflow (OIDC on tag), release-please auto-versioning |
| Dev scripts | 3 | dev-setup.sh, deploy-infra.sh, teardown-dev.sh |

### Live Validation Results (2026-03-24)

All extractors tested against real Microsoft Graph API with a live Entra app registration.

**Probe results (dry-run)**:
- 7/7 probes pass after fixing `teams_channels` `$top` parameter rejection

**Extractor sync results**:
- 6/6 enabled extractors sync successfully against real Graph API
- `teams_channels` disabled — requires `Channel.ReadBasic.All` admin consent (not just `ChannelMessage.Read.All`)
- Incremental sync verified: email delta tokens, calendar skip-unchanged, OneDrive delta

**SharePoint scale test**:
- 25K items across 100 pages synced
- Initial delta does not complete in 100 pages (`max_pages: 100` config limit) — subsequent syncs pick up via delta token

**Edge cases discovered and fixed**:
- 13 new edge-case tests added during live validation

### Bugs Found During Live Validation

| Bug | Fix | Commit |
|-----|-----|--------|
| `/me/joinedTeams` rejects `$top` query param | Removed `$top` from probe and extractor | Live validation session |
| Contacts delta endpoint rejects `$select`/`$top` | Removed unsupported params from contacts extractor | Live validation session |
| Null `from` field in email crashes `_write_email` | Added `or {}` guard pattern | Live validation session |

### What's Missing (Roadmap Phase 5D–5F)

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 5A | Local web service testing | Done (2026-03-24) |
| Phase 5B | Reflex admin dashboard MVP | Done (2026-03-24) |
| Phase 5C | Daemon integration + sync visibility | Done (2026-03-25) |
| Phase 5E | Azure deployment (dev) | Done (2026-03-25) — end-to-end working |
| Phase 5E | Azure deployment (prod) | Pending — Entra redirect URI, GitHub secrets, tag v0.3.0. See roadmap for checklist. |
| Phase 5D | Security + RBAC | Not started — Key Vault integration, rate limiting, audit logging, session timeout |
| Phase 5F | Graph webhooks | Not started — deferred, polling sufficient for current scale |
| Future | MCP server integration | Not started |

### Entra App Registration Permissions

The app (`workflow-read`) requires delegated Graph API permissions. Some require admin consent. Only request scopes for extractors you intend to enable — requesting an ungranted scope blocks the entire login flow.

| Scope | Extractor | Admin consent? | Notes |
|-------|-----------|----------------|-------|
| `User.Read` | (all) | No | Token validation |
| `Mail.Read` | email | No | |
| `Calendars.Read` | calendar | No | |
| `Chat.Read` | teams_chats | No | |
| `ChannelMessage.Read.All` | teams_channels | Yes | Also needs `Channel.ReadBasic.All` for channel listing |
| `Channel.ReadBasic.All` | teams_channels | Yes | Required to list channels within teams |
| `Files.Read.All` | onedrive | Yes | |
| `Sites.Read.All` | sharepoint | Yes | |
| `Contacts.Read` | contacts | No | |
| `User.Read.All` | directory | Yes | Read user profiles |
| `Directory.Read.All` | directory | Yes | Manager chain + direct reports traversal |
| `offline_access` | (all) | No | Persistent token refresh |

## Architecture Quality

The codebase follows the workspace engineering principles well:

- **No defaults**: All config from YAML, crashes on missing values. No default function arguments.
- **Fail fast**: `ConfigError` with clear messages on config errors. Narrow exception catches (GraphApiError, ExtractorError).
- **Modular**: Each extractor is a standalone module; storage backends are pluggable via protocol
- **Frozen pydantic models**: Config immutability enforced at the type level, including typed ConvertersConfig
- **Composable config**: 10 YAML fragments in `config/`, deep-merge loader, eliminates 95% duplication across environments
- **Shared helpers**: `_message_helpers.py` extracted for Teams extractors, `_execute_with_retry` extracted for retry logic in graph_client.py
- **File size discipline**: All source files under 200 lines (cli.py split into cli + dry_run + continuous; frontmatter.py split into per-type package)
- **Deferred imports**: Optional deps (azure-storage-blob, obsidian-import) imported at call time
- **Token provider abstraction**: GraphClient decoupled from auth flow
- **Delta sync**: Incremental sync via Graph API delta tokens, state persisted in JSON
- **Custom exceptions**: `GraphApiError` used instead of bare `RuntimeError`
- **Path traversal protection**: LocalBackend validates paths to prevent directory escape
- **Restrictive permissions**: MSAL token cache set to 0600

## Where m365-extract is Ahead of Reference Repos

| Dimension | m365-extract | Reference repos |
|-----------|-------------|-----------------|
| Docker | Dockerfile + docker-compose.yaml (with azurite profile) | None |
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

## Multi-User Web Service Gap Assessment

### What Works for Local/Internal Testing (1-3 users)

- OAuth2 login flow with Entra (authorization code + PKCE)
- Per-user token encryption at rest (Fernet + SQLite)
- Per-user storage isolation (`vault/{user_id}/`)
- Background sync scheduler (APScheduler, per-user jobs)
- Admin user management (list, enable, disable, delete)
- Session-based access control (user can only trigger own sync)
- Thread-safe token refresh with locking

### Critical Gaps for Production

| Gap | Severity | Impact |
|-----|----------|--------|
| Admin endpoints unauthenticated | Critical | Partially addressed — `admin_emails` config restricts admin pages in Reflex UI |
| No RBAC | Medium | Basic admin/user distinction via `admin_emails`; full RBAC (roles, middleware) deferred to Phase 5D |
| Sync state shared | Done | Per-user sync state via `state/{user_id}/sync_state.json` (daemon.py) |
| `_last_sync` in-memory | Done | SyncRecord table persists sync history; UI reads from database |
| No audit logging | Medium | No trail of auth/admin actions |
| No rate limiting | Medium | Auth endpoints vulnerable to brute force |
| ~~No DB migrations~~ | ~~Medium~~ | Done — Alembic migrations via Reflex scaffolding |
| No secrets rotation | Low | Fernet key, session secret — no rotation strategy |

### Hardening Roadmap

**Phase 5A (local testing)**: Done. Entra app configured, OAuth2 login verified, callback error handling fixed.

**Phase 5B (Reflex dashboard MVP)**: Scaffold `web-ui/` Reflex project, Entra OAuth2 via Reflex state + MSAL, dashboard with extractor toggles + sync trigger, admin user list.

**Phase 5C (sync visibility)**: Sync history SQLite table + UI, per-user scheduling, persistent `_last_sync`, real-time WebSocket updates.

**Phase 5D (security + RBAC)**: Admin vs user roles, admin-only routes, rate limiting, audit logging, session timeouts.

**Phase 5E (deployment)**: Dockerized Reflex app, Bicep IaC for App Service + ACR + Key Vault, managed identity, OIDC for GitHub Actions, production Entra URIs + CORS.
