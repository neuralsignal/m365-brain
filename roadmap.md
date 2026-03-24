# m365-extract Roadmap

## Phase 1: Core Library + CLI (single-user, local storage) -- DONE

`pip install m365-extract && m365-extract sync --once` works end-to-end against real Graph API.

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
2. Run CLI locally: `m365-extract auth login` then `m365-extract sync --once`
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
2. Run: `m365-extract sync --once --extractors onedrive,sharepoint`
3. Verify DOCX/PPTX/PDF → markdown conversion quality
4. Test large files for timeout/size limits

---

## Phase 3: Azure Blob Storage + Docker

**Goal**: `docker run m365-extract` syncs to Azure Blob Storage.

**Implementation**:
1. `storage/azure_blob.py` — Azure Blob Storage backend with per-user prefix routing
2. Dockerfile (python:3.12-slim, multi-stage, non-root user)
3. `docker-compose.yaml` (service + Azurite emulator for local dev)
4. Integration tests against Azurite

**New dependencies**: `azure-storage-blob>=12.24,<13`

### Stopping Point 3: Azure Resource Provisioning

1. Create resource group: `az group create --name rg-m365-extract --location switzerlandnorth`
2. Create storage account: `az storage account create --name stm365extract --resource-group rg-m365-extract --location switzerlandnorth --sku Standard_LRS --kind StorageV2`
3. Create blob container: `az storage container create --name m365-vaults --account-name stm365extract`
4. Test Docker build + Azure Blob: `docker build -t m365-extract . && docker run --env-file .env m365-extract sync --once`

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
5. Test multi-user locally: `m365-extract serve --config config.web.yaml`

---

## Phase 5: Reflex Admin Dashboard + Deployment

**Goal**: Replace the headless FastAPI JSON API with a full-stack admin dashboard using [Reflex.dev](https://reflex.dev) (Python → React frontend + FastAPI backend). Non-technical users visit a URL, authenticate via Entra, choose extractors, see sync status, and manage their configuration.

**Status**: Phase 4 built the complete web service backend (FastAPI, OAuth2, per-user isolation, scheduler). CI/CD infrastructure is complete (18 GitHub Actions workflows). Live validation (2026-03-24) confirmed OAuth2 login, user creation, health endpoint, and all extractors against real Graph API. The core gap: **no UI** — Phase 4 delivered API endpoints only.

**Architecture decision**: m365-extract stays as a library/CLI (extractors, sync API, auth, config, storage, Graph client). A new Reflex app (`web-ui/`) imports m365-extract as a dependency and adds the UI, RBAC, scheduling, and deployment layers. This keeps the PyPI package lean for CLI-only users and lets the UI evolve independently.

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

### Phase 5B: Reflex Admin Dashboard MVP

Scaffold the Reflex app and deliver a working dashboard that replaces the headless API.

**Structure**: `web-ui/` directory with its own `pixi.toml` (Reflex is PyPI-only, not conda-forge).

1. Scaffold `web-ui/` Reflex project (`pixi.toml`, `rxconfig.py`, app structure)
2. Entra OAuth2 login via Reflex state + MSAL (replace current FastAPI session auth)
3. Post-login dashboard: user profile, enabled extractors, last sync time
4. Extractor toggle UI (wire up `extractor_preferences` from `UserManager`)
5. Manual sync trigger button with progress/status feedback
6. Admin view: user list, enable/disable users
7. Per-user sync state isolation (move from global JSON to `state/{user_id}/`)

### Phase 5C: Sync Visibility + Per-User Scheduling

Make sync history visible and give users control over their sync cadence.

1. Sync history table (SQLite: `user_id`, `started_at`, `status`, `items_synced`, `errors`)
2. Sync history UI (table with filtering, error details)
3. Per-user scheduling (interval override per user, stored in user preferences)
4. Persistent sync status (move `_last_sync` from in-memory to database)
5. Real-time sync progress via Reflex WebSocket state updates

### Phase 5D: Security + RBAC

Harden for multi-user production use.

1. Role-based access: admin vs user (Entra app roles or first-user-is-admin)
2. Admin-only routes (user management, global config)
3. Rate limiting on auth endpoints
4. Audit logging (structured events for auth/admin/sync actions)
5. Session timeout enforcement

### Phase 5E: Azure Deployment

Deploy the Reflex app to Azure App Service.

1. Dockerized Reflex app (frontend build + backend + nginx reverse proxy)
2. Bicep IaC (App Service + ACR + Key Vault + Storage Account)
3. Managed Identity for Azure Blob Storage
4. OIDC federated identity for GitHub Actions deploy
5. Production Entra redirect URIs and CORS
6. Health probes for App Service

### Phase 5F: Graph Webhooks (deferred)

Polling is sufficient for current scale. Webhooks add complexity without proportional benefit until user count grows.

1. Graph change notification subscriptions
2. Webhook endpoint to receive push notifications
3. Subscription renewal lifecycle

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
- Test count: 158 → 247 across 23 test files

---

## Future: MCP Server for Claude Code

Separate package: `m365-extract-mcp`

- Tools: `search(query)`, `read(path)`, `list(prefix)`
- Connects to Azure Blob Storage (or local filesystem)
- Authenticates as the user, enforces data isolation
- Allows Claude Code to search/grep the synced vault from any machine
