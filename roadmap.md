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

## Phase 5: Web UI Testing + Security Hardening + Deployment -- PARTIAL

**Goal**: Validate the web service end-to-end, harden for multi-user, deploy to Azure.

**Status**: Phase 4 built the complete web service (FastAPI, OAuth2, per-user isolation, scheduler). CI/CD infrastructure is complete (18 GitHub Actions workflows). Live validation (2026-03-24) confirmed all extractors work against real Graph API. Phase 5 is split into three sub-phases.

**Done (infrastructure)**:
- GitHub Actions CI/CD workflows (lint, test, coverage, release-please, PyPI publish, docs deploy)
- Dark factory agent workflows (code-quality, test-coverage, security-scan, dep-audit, docs-freshness, issue-triage, PR-review, PR-autofix)
- `sync.py` — public sync API extracted from CLI layer (web + CLI both import from here)
- `web/middleware.py` — per-user access control (session user must match URL user_id, returns 403)
- Per-user storage isolation — web mode writes to `vault/{user_id}/` instead of shared `vault/`
- `expires_at` token computation in auth callback — prevents unnecessary refresh on every request
- `config.yaml` — `client_secret: null` for CLI mode compatibility
- `.env.example` — template for environment variables

### Phase 5A: Local Web UI Testing

**Goal**: First person ever logs in through the web UI against a real Entra app.

**Prerequisites** (manual, in Azure Portal):
1. Add client secret: Entra > App registrations > `workflow-read` > Certificates & secrets > New
2. Add redirect URI: Entra > Authentication > Add platform > Web > `http://localhost:8000/auth/callback`
3. Verify "Allow public client flows" still enabled (needed for CLI device code flow too)

**Environment variables** (add to `.env`):
```
AZURE_CLIENT_ID=<same as MSAL_CLIENT_ID>
AZURE_TENANT_ID=<same as MSAL_TENANT_ID>
AZURE_CLIENT_SECRET=<from step 1>
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(32))">
FERNET_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

**Test flow**:
1. `m365-extract --config config.web.yaml serve` → Uvicorn on 0.0.0.0:8000
2. `http://localhost:8000/auth/login` → Entra redirect
3. Log in → callback returns `{"status": "authenticated", "user_id": "<oid>"}`
4. `GET /admin/users` → shows authenticated user
5. `POST /sync/<user_id>` → vault output in `vault/<user_id>/`
6. `GET /health` → 200 OK

**Known risks**:
- Redirect URI mismatch: `request.url_for("callback")` may generate `http://0.0.0.0:8000/...` instead of `http://localhost:8000/...`
- `config.web.yaml` uses `AZURE_*` env vars while `config.yaml` uses `MSAL_*` — both reference the same Entra app

### Phase 5B: Security Hardening (future)

1. Admin endpoint authentication (API key or Entra role check)
2. Per-user sync state files (`state/{user_id}/sync_state.json`)
3. Persistent `_last_sync` (move from in-memory dict to database)
4. Rate limiting on auth endpoints (FastAPI SlowAPI)
5. Audit logging (structured events for auth/admin actions)

### Phase 5C: Azure Deployment (future)

1. Bicep IaC for App Service + Container Registry + Key Vault
2. Managed Identity for Azure Blob Storage
3. OIDC federated identity for GitHub Actions deploy
4. Production redirect URIs and CORS
5. Health probes for App Service
6. Graph webhooks (`web/routes_webhooks.py`, subscription management)
7. Test with 2-3 users

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
