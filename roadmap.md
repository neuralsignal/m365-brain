# m365-brain Roadmap

## Phase 1: Core Library + CLI (single-user, local storage) -- DONE

`pip install m365-brain && m365-brain sync --once` works end-to-end against real Graph API.

- Project scaffold: pyproject.toml, pixi.toml, config.yaml
- config.py — frozen dataclass config loader, strict validation, env var expansion
- graph_client.py — httpx + pagination + retry + rate limit + delta queries
- auth/device_code.py — MSAL device code flow
- state.py — sync state persistence (delta tokens, timestamps)
- storage/base.py — StorageBackend protocol
- storage/local.py — local filesystem implementation
- markdown_writer.py — frontmatter builders + slugify/short_hash
- converters/html_to_md.py — markdownify HTML→markdown
- extractors: email (delta), calendar (calendarView), teams_chats (filter), teams_channels (delta)
- cli.py — Click CLI with sync and auth commands
- 79 unit tests with pytest-httpx mocks

### Stopping Point 1: User Testing + Graph API Validation

1. Test Graph API access via Graph Explorer
2. Run CLI locally: `m365-brain auth login` then `m365-brain sync --once`
3. Verify output in `./vault/`
4. Verify incremental sync: run again, confirm only new items fetched

---

## Phase 2: File Extractors + Document Conversion

**Goal**: OneDrive and SharePoint sync with obsidian-import conversion.

**Implementation**:
1. `converters/document.py` — obsidian-import wrapper with configurable backend selection (native, markitdown, docling)
2. `extractors/onedrive.py` — delta sync, catalog-first, eager/on-demand convert
3. `extractors/sharepoint.py` — auto-discover accessible sites via `/me/followedSites`, per-site delta sync
4. Update config schema for conversion settings + eager convert patterns + backend selection

**New dependencies**: `obsidian-import[markitdown,docling]`

### Stopping Point 2: File Conversion Validation

1. Test via Graph Explorer: `GET /me/drive/root/children`, `GET /me/followedSites`
2. Run: `m365-brain sync --once --extractors onedrive,sharepoint`
3. Verify DOCX/PPTX/PDF → markdown conversion quality
4. Test large files for timeout/size limits

---

## Phase 3: Azure Blob Storage + Docker

**Goal**: `docker run m365-brain` syncs to Azure Blob Storage.

**Implementation**:
1. `storage/azure_blob.py` — Azure Blob Storage backend with per-user prefix routing
2. Dockerfile.web + Dockerfile.daemon (python:3.12-slim, multi-stage, non-root user)
3. `docker-compose.yaml` (full stack + Azurite emulator via profile)
4. Integration tests against Azurite

**New dependencies**: `azure-storage-blob>=12.24,<13`

### Stopping Point 3: Azure Resource Provisioning

1. Create resource group: `az group create --name rg-m365-brain --location switzerlandnorth`
2. Create storage account: `az storage account create --name stm365extract --resource-group rg-m365-brain --location switzerlandnorth --sku Standard_LRS --kind StorageV2`
3. Create blob container: `az storage container create --name m365-vaults --account-name stm365extract`
4. Test Docker build + Azure Blob: `docker build -t m365-brain . && docker run --env-file .env m365-brain sync --once`

---

## Phase 4: Multi-User Web Service

**Goal**: Non-technical users visit a URL, authenticate via Entra, choose extractors, sync starts.

**Implementation**:
1. `auth/auth_code.py` — MSAL ConfidentialClientApplication, authorization code flow
2. `auth/token_store.py` — SQLite + Fernet encryption for multi-user token storage
3. `web/app.py` — FastAPI app factory
4. `web/routes_auth.py` — `/auth/login`, `/auth/callback`
5. `web/routes_admin.py` — `/admin/users`, `/admin/sync`
6. `web/routes_health.py` — `/health`
7. `web/middleware.py` — per-user access control (users can only access own data)
8. `user_manager.py` — user CRUD, preference storage (which extractors enabled)
9. `scheduler.py` — APScheduler for per-user sync jobs

**New dependencies**: `fastapi`, `uvicorn`, `apscheduler`, `cryptography`

### Stopping Point 4: Entra App Configuration for Web Mode

1. Add client secret to Entra app (Certificates & secrets → New)
2. Add redirect URI: `http://localhost:8000/auth/callback` (dev)
3. Verify "Allow public client flows" still enabled
4. Generate Fernet token encryption key
5. Test multi-user locally: `m365-brain serve --config config.web.yaml`

---

## Phase 5: Reflex Admin Dashboard + Deployment

**Goal**: Replace the headless FastAPI JSON API with a full-stack admin dashboard using [Reflex.dev](https://reflex.dev) (Python → React frontend + FastAPI backend). Non-technical users visit a URL, authenticate via Entra, choose extractors, see sync status, and manage their configuration.

**Status**: Phase 4 built the complete web service backend (FastAPI, OAuth2, per-user isolation, scheduler). CI/CD infrastructure is complete (18 GitHub Actions workflows). Live validation (2026-03-24) confirmed OAuth2 login, user creation, health endpoint, and all extractors against real Graph API. The core gap: **no UI** — Phase 4 delivered API endpoints only.

**Architecture decision**: m365-brain stays as a library/CLI (extractors, sync API, auth, config, storage, Graph client). A new Reflex app (`web-ui/`) imports m365-brain as a dependency and adds the UI, RBAC, scheduling, and deployment layers. This keeps the PyPI package lean for CLI-only users and lets the UI evolve independently.

**Done (infrastructure)**:
- GitHub Actions CI/CD workflows (lint, test, coverage, release-please, PyPI publish, docs deploy)
- Dark factory agent workflows (code-quality, test-coverage, security-scan, dep-audit, docs-freshness, issue-triage, PR-review, PR-autofix)
- `sync.py` — public sync API extracted from CLI layer (web + CLI both import from here)
- `web/middleware.py` — per-user access control (session user must match URL user_id, returns 403)
- Per-user storage isolation — web mode writes to `vault/{user_id}/` instead of shared `vault/`
- `expires_at` token computation in auth callback — prevents unnecessary refresh on every request
- `config.yaml` — `client_secret: null` for CLI mode compatibility
- `.env.example` — template for environment variables

### Phase 5A: Local Web Service Testing -- DONE

First person ever logs in through the web service against a real Entra app.

**Completed (2026-03-24)**:
- Entra app configured: client secret, Web platform, redirect URI (`http://localhost:8000/auth/callback`)
- OAuth2 login → callback → user creation: verified working against real Entra
- Fixed callback to handle Entra error responses (`code` and `state` params made optional)
- `/admin/users`, `/health` endpoints verified
- Documented multi-user gaps in MATURITY.md

### Phase 5B: Reflex Admin Dashboard MVP -- DONE

Replaced headless FastAPI API with a full Reflex admin dashboard.

**Completed (2026-03-24)**:
- `m365_admin/` package with 5 SQLModel tables, 4 state classes, 6 pages, sidebar navigation
- Entra OAuth2 login via Reflex state + MSAL
- Dashboard: user profile, enabled extractors, last sync time
- Extractor toggle UI (ExtractorPreference model, PreferencesState)
- Sync history table (SyncRecord model, SyncState page)
- Admin view: user list, enable/disable users, admin config management
- Services: TokenService (Fernet encrypt/decrypt), AdminService (config CRUD, role check)
- 74 admin tests passing
- Deleted old FastAPI web layer (`m365_brain/web/`, `user_manager.py`, `token_store.py`)

### Phase 5C: Daemon Integration + Sync Visibility -- DONE

Connect the sync daemon to the database so the UI shows real sync data.

**Completed (2026-03-25)**:
- `m365_brain/daemon.py` — daemon sync runner (get_enabled_users, sync_user, run_daemon_cycle, write_sync_record, write_health_file)
- `TokenStoreProtocol` in `token_provider.py` — replaces deleted `TokenStore` import with Protocol
- `TokenServiceAdapter` in `token_service.py` — bridges TokenService to TokenStoreProtocol for daemon
- CLI `daemon` command — `m365-brain --config config.web.yaml daemon` (replaced by `worker` command in Phase 5G)
- Per-user sync state: `state/{user_id}/sync_state.json`
- SyncRecord written at start (running) and completion (completed/failed)
- `seed_admin_config()` call at engine startup (idempotent)
- Daemon health file (`state/daemon_health.json`) written after each cycle for Docker HEALTHCHECK
- 9 daemon tests + 3 adapter tests passing

**Deferred (not blocking deployment)**:
1. Per-user scheduling (interval override per user, stored in user preferences)
2. Real-time sync progress via Reflex WebSocket state updates
3. Sync history UI filtering and error details

**Superseded:** The daemon architecture from Phase 5C was replaced in Phase 5G by an independent worker with per-(user, extractor) jobs.

### Phase 5D: Security + RBAC

Harden for multi-user production use.

**6a. Key Vault integration** (half day)
- Store secrets in Key Vault via Bicep (`Microsoft.KeyVault/vaults/secrets`)
- App Service: replace appSettings values with Key Vault references (`@Microsoft.KeyVault(SecretUri=...)`)
- ACI: keep secrets in env vars (ACI doesn't support KV references natively)
- Prerequisite: Key Vault RBAC assignment for App Service managed identity

**6b. Rate limiting** (2 hours)
- Add `slowapi` or Reflex middleware for auth endpoints
- Config: `web.rate_limit_per_minute: 10` for login/callback

**6c. Audit logging** (half day)
- Structured events for: login, logout, extractor toggle, sync trigger, user enable/disable
- Write to structlog (flows to Log Analytics via WP3)
- New `AuditEvent` SQLModel table: `user_id, action, details_json, timestamp`

**6d. Session timeout enforcement** (1 hour)
- `web.session_timeout_minutes` already in config but not enforced in Reflex state
- Add expiry check in `AuthState.on_load()`

### Phase 5E: Azure Deployment -- DONE (dev), PENDING (prod)

Deploy the Reflex app to Azure App Service.

**Completed (2026-03-25)**:
- `Dockerfile.web` fixed: Caddy reverse proxy + Reflex backend, single-port deployment
- `Dockerfile.daemon` fixed: real health check via `scripts/daemon_healthcheck.py`
- `docker-compose.yaml` for full-stack local testing with PostgreSQL + Azurite profile
- `infra/main.bicep`: Storage, ACR, PostgreSQL, App Service, Container Instance, Key Vault, Log Analytics, diagnostic settings
- `deploy.yml`: push to main → dev, tag push → prod, all secrets passed to Bicep
- Dev environment deployed and working end-to-end (OAuth login, dashboard, daemon sync to blob)

**Completed (2026-03-26 — config + code quality refactor)**:
- Composable config: 5 monolithic YAML files replaced with 10 composable fragments in `config/`
- Constitution alignment: bare exceptions narrowed, prints replaced with ConfigError, overlong files split, default args removed
- Observability: Log Analytics workspace + diagnostic settings for App Service and PostgreSQL
- DB migrations: Alembic initialized via Reflex, initial schema migration, replaces `create_all()`
- Simplification: base Dockerfile deleted, docker-compose consolidated with Azurite profile
- Typed converters config: `ConvertersConfig` pydantic model with `slug_max_length`, `hash_length`
- Unified Dockerfile: `Dockerfile.web` and `Dockerfile.daemon` consolidated into single `Dockerfile` with worker support

**Remaining — Prod deployment checklist**:

1. **Entra redirect URI**: Add `https://app-m365-admin-prod.azurewebsites.net/callback` to the Entra app registration. Use `az ad app update --id <app-id> --web-redirect-uris` — this **replaces** all URIs, so include all existing ones:
   ```bash
   az ad app update --id <client-id> \
     --web-redirect-uris \
       "http://localhost:8000/callback" \
       "https://app-m365-admin-dev.azurewebsites.net/callback" \
       "https://app-m365-admin-prod.azurewebsites.net/callback"
   ```

2. **GitHub prod secrets**: Create a `prod` environment in GitHub repo settings. Set environment-level secrets:
   - `POSTGRES_ADMIN_PASSWORD` — different from dev
   - `SECRET_KEY` — generate: `python -c "import secrets; print(secrets.token_hex(32))"`
   - `FERNET_KEY` — generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - Non-secret vars (`ENTRA_CLIENT_ID`, `AZURE_TENANT_ID`, `ADMIN_EMAIL`) can be shared or per-environment

3. **Deploy**: Tag and push to trigger the prod deploy workflow:
   ```bash
   git tag v0.3.0
   git push origin v0.3.0
   ```
   `deploy.yml` triggers on `v*` tags with `environment=prod` and uses `infra/params.prod.bicepparam` (90-day log retention).

4. **Post-deploy verification**:
   - OAuth login on `https://app-m365-admin-prod.azurewebsites.net`
   - App Service logs: `az webapp log tail --name app-m365-admin-prod --resource-group rg-m365-brain-prod` (no separate daemon container — worker runs as a thread within the App Service)
   - Sync history visible in dashboard
   - Log Analytics receiving data: `az monitor log-analytics query --workspace <id> --analytics-query "AppServiceConsoleLogs | take 5"`

5. **DNS/custom domain**: Optional — `*.azurewebsites.net` works. Custom domain if needed later.

### Phase 5G: Worker Refactor -- DONE

Replaced the monolithic daemon thread with an independent sync worker.

**Completed (2026-03-26)**:
- `m365_brain/worker.py` — independent worker with per-(user, extractor) jobs via `ThreadPoolExecutor`
- Each (user, extractor) pair runs as a separate job with its own token provider, storage, and sync state
- PostgreSQL advisory locks prevent duplicate runs across worker instances
- `ExtractorStatus` model replaces `SyncRecord` — single row per (user, extractor) showing latest status
- `WorkerConfig` added to config schema (`max_concurrent_jobs`, `poll_interval_seconds`)
- New CLI command: `m365-brain worker` — standalone worker process for multi-user scheduling
- `start_worker_thread()` bridge for single-container Azure App Service deployment
- Dashboard shows per-extractor status grid instead of sync history log
- docker-compose updated with separate `worker` service
- 12 new worker tests, 386 total tests passing

**Deleted**:
- `m365_brain/daemon.py` — replaced by `worker.py`
- `m365_brain/continuous.py` — replaced by `worker` command
- `m365_admin/daemon_runner.py` — replaced by `worker.start_worker_thread()`
- `SyncRecord` model — replaced by `ExtractorStatus`
- CLI `--continuous` flag — replaced by `worker` command

**Architecture**:
```
Reflex App (UI only)     PostgreSQL (shared)     Worker Process
- User OAuth          -> user, tokenrecord    <- m365-brain worker
- Preferences         -> extractorpreference  <- Poll loop
- Status grid         -> extractorstatus      <- ThreadPoolExecutor
- Admin panel                                 <- Per (user, extractor) jobs
```

**Future path**: When extraction gets compute-heavy (OCR, ML models), replace `ThreadPoolExecutor` with Celery + Redis workers. The job interface (`run_single_extractor` with serializable args) is designed for this migration.

### Phase 5F: Graph Webhooks (deferred)

Polling is sufficient for current scale. Webhooks add complexity without proportional benefit until user count grows.

1. Graph change notification subscriptions
2. Webhook endpoint to receive push notifications
3. Subscription renewal lifecycle

The worker architecture (Phase 5G) provides the foundation — webhook notifications would trigger individual (user, extractor) jobs instead of the current polling approach.

---

## Phase 6: Contacts + Directory -- DONE

**Prerequisites**: `Contacts.Read`, `User.Read.All`, and `Directory.Read.All` permissions granted in Entra admin center.

1. `extractors/contacts.py` — personal contacts with delta sync ✓
2. `extractors/directory.py` — GAL full refresh ✓

Implemented and merged in v0.3.0-pending (commit `4666ef0`).

### Entra Permissions for Directory Extractor

The directory extractor requires two scopes:

| Scope | Why | Admin consent? |
|-------|-----|----------------|
| `User.Read.All` | Read all user profiles via `/users/delta` (displayName, email, jobTitle, department, etc.) | Yes |
| `Directory.Read.All` | Traverse manager chain (`/users/{id}/manager`) and direct reports (`/users/{id}/directReports`) | Yes |

Both require admin consent. `Directory.Read.All` must be added as a **delegated** permission on the Entra app registration and explicitly granted by a Global Administrator.

**Note:** The directory extractor is disabled by default. Only add `Directory.Read.All` to the app registration when you intend to enable it. Requesting this scope before it is granted blocks the entire device code login flow.

---

## Bonus: Dark Factory Infrastructure -- DONE

_Completed 2026-03-17 to 2026-03-23 by autonomous Claude agents._

Not part of the original roadmap. The dark factory loop (scan → triage → implement → review → merge) was set up during the initial extraction and has been running autonomously since. Results:

- 50+ commits, 4 releases (0.1.0 → 0.2.2)
- 18 GitHub Actions workflows
- ruff + pre-commit, MkDocs + Material, pytest-cov 82%+, release-please
- CLAUDE.md, README.md, CHANGELOG.md, LICENSE
- Config package split, shared helpers extraction, dead code removal
- Path traversal protection, token cache hardening, CVE fix
- Test count: 158 → 247 → 386 across 37 test files

---

## Future: MCP Server for Claude Code

Separate package: `m365-brain-mcp`

- Tools: `search(query)`, `read(path)`, `list(prefix)`
- Connects to Azure Blob Storage (or local filesystem)
- Authenticates as the user, enforces data isolation
- Allows Claude Code to search/grep the synced vault from any machine
