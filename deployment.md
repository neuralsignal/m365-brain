# m365-extract Deployment Guide

Tracks Azure infrastructure setup, deployment steps, gotchas, and current state. This document captures every manual step needed to reproduce the deployment from scratch.

## Order of Operations (Complete Deployment from Scratch)

This is the end-to-end sequence. Each step depends on previous steps completing successfully. Do not skip or reorder.

### Phase 1: Azure Foundation

1. **Log in to Azure CLI**: `az login --tenant ea0bd7d3-b29f-47f4-aedc-da7b52a28ba0`
2. **Verify subscription**: `az account set --subscription 9f079696-135d-4d18-a208-3e2e55fca2f5`
3. **Register resource providers** (one-time, async -- may take minutes):
   ```bash
   az provider register --namespace Microsoft.Storage
   az provider register --namespace Microsoft.ContainerRegistry
   az provider register --namespace Microsoft.DBforPostgreSQL
   az provider register --namespace Microsoft.Web
   az provider register --namespace Microsoft.KeyVault
   ```
   Wait until all show `Registered` before proceeding.

### Phase 2: Identity Setup

4. **Create the Entra app registration** (user auth -- `workflow-read`) with redirect URIs and Graph API permissions (see [Entra App Registration](#entra-app-registration-user-auth) section)
5. **Create the deploy service principal** (`sp-m365-extract-deploy`) with Contributor role (see [Service Principal](#service-principal-cicd-deploy) section)
6. **Add OIDC federated credentials** on the app registration object (not the SP) for GitHub Actions (see [OIDC Federated Credentials](#oidc-federated-credentials) section)

### Phase 3: GitHub Configuration

7. **Create GitHub environments**: `dev` and `prod`
8. **Set all GitHub secrets** (9 secrets -- see [GitHub Secrets](#secrets-configured-2026-03-25) table)

### Phase 4: Build and Push Images FIRST

**Critical: Push the image before the first Bicep deploy.** App Service will fail to pull if the image does not exist yet in ACR. On the very first deploy, manually create the ACR and push the image before running Bicep:

9. **Create resource group and ACR manually** (first time only):
   ```bash
   az group create --name rg-m365-extract-dev --location switzerlandnorth
   az acr create --name acrm365extdev --resource-group rg-m365-extract-dev \
     --sku Basic --admin-enabled true
   ```
10. **Build and push Docker images** with a unique tag (never rely on `:latest` for ACI):
    ```bash
    bash scripts/build-and-push.sh dev --tag $(date +%Y%m%d%H%M%S)
    ```

### Phase 5: Infrastructure Deployment

11. **Deploy Bicep** (creates all remaining resources -- PostgreSQL, App Service, Key Vault, Storage):
    ```bash
    bash scripts/deploy-infra.sh dev
    ```
12. **Post-deployment: Assign Key Vault RBAC** (manual, requires Owner -- see [Key Vault RBAC](#key-vault-rbac-manual-post-deployment) section)

### Phase 6: Verification

13. **Update App Service container image** (if Bicep used a stale image reference):
    ```bash
    az webapp config container set --name app-m365-admin-dev \
      --resource-group rg-m365-extract-dev \
      --container-image-name acrm365extdev.azurecr.io/m365-admin:<your-tag>
    az webapp restart --name app-m365-admin-dev --resource-group rg-m365-extract-dev
    ```
14. **Smoke test**:
    - `https://app-m365-admin-dev.azurewebsites.net/ping` returns 200
    - `https://app-m365-admin-dev.azurewebsites.net/` returns 200 (frontend)
    - OAuth login flow completes to `/dashboard`

### Subsequent Deploys (CI/CD or Manual)

For updates after the initial deployment:

1. Build and push the image with a **unique tag** (timestamp or SHA)
2. Re-deploy Bicep (idempotent) or use `az webapp config container set` with explicit image + credentials
3. Restart App Service

---

## Azure Account

| Item | Value |
|------|-------|
| Tenant | Sanoptis (`ea0bd7d3-b29f-47f4-aedc-da7b52a28ba0`) |
| Subscription | `sub-san-mdai` (`9f079696-135d-4d18-a208-3e2e55fca2f5`) |
| Role | Owner (upgraded 2026-03-25; was Contributor) |
| Region | `switzerlandnorth` |

## Entra App Registration (User Auth)

This is the app users authenticate against (OAuth2 authorization code flow). **Not** the deploy service principal.

| Item | Value |
|------|-------|
| App name | `workflow-read` |
| App ID (client ID) | `f209d856-e14d-4dcf-87cc-bf0d98bb092b` |
| Client secret name | `reader` (expires 2028-03-04) |
| Redirect URIs | See below |

### Redirect URIs (configured 2026-03-25)

```
http://localhost:3000/callback        <- local dev (pixi run -e admin dev)
http://localhost:8000/auth/callback   <- local web mode
https://app-m365-admin-dev.azurewebsites.net/callback  <- Azure dev
```

**How to update redirect URIs:**
```bash
az ad app update --id f209d856-e14d-4dcf-87cc-bf0d98bb092b \
  --web-redirect-uris \
    "http://localhost:3000/callback" \
    "http://localhost:8000/auth/callback" \
    "https://app-m365-admin-dev.azurewebsites.net/callback"
```

**Gotcha:** `az ad app update --web-redirect-uris` is a **replace** operation, not append. Always include all existing URIs in the command or they will be removed.

### Graph API Permissions (delegated)

User.Read, Mail.Read, Mail.ReadBasic, Mail.ReadWrite, Mail.Send, Calendars.Read, Contacts.Read, Files.Read.All, Sites.Read.All, Chat.Read, Chat.ReadBasic, ChannelMessage.Read.All, Channel.ReadBasic.All, ChannelSettings.Read.All, Team.ReadBasic.All, TeamMember.Read.All, User.ReadBasic.All, offline_access, openid, profile, email

### Graph API Permissions (application)

User.Read.All

## Service Principal (CI/CD Deploy)

Separate from the Entra app above. Used only by GitHub Actions for OIDC login + Azure resource management.

| Item | Value |
|------|-------|
| Display name | `sp-m365-extract-deploy` |
| App ID (client ID) | `e31a8416-7cd9-4b71-9d7e-7f89cbd7631a` |
| Object ID (SP) | `0c9fe08b-b292-4954-920d-5a7bdeeb7a06` |
| Object ID (App reg) | `d471c1e7-77bc-4b2b-a58c-e42ab4546bf9` |
| Role | Contributor on subscription |
| Created | 2026-03-25 |

### How the SP was created

```bash
# Step 1: Create SP (this also creates the app registration)
# Note: --role + --scopes requires User Access Administrator or Owner.
# If you only have Contributor, the SP is created but the role assignment fails.
az ad sp create-for-rbac --name "sp-m365-extract-deploy" \
  --role Contributor \
  --scopes /subscriptions/9f079696-135d-4d18-a208-3e2e55fca2f5

# Step 2 (if role assignment failed): Assign role separately as Owner
az role assignment create \
  --assignee e31a8416-7cd9-4b71-9d7e-7f89cbd7631a \
  --role Contributor \
  --scope /subscriptions/9f079696-135d-4d18-a208-3e2e55fca2f5
```

**Gotcha:** `az ad sp create-for-rbac` creates the SP and app reg even if the role assignment fails (exits non-zero). Check `az ad sp list --display-name "sp-m365-extract-deploy"` to confirm.

### OIDC Federated Credentials

GitHub Actions authenticates to Azure via OIDC (no stored client secrets). Three federated credentials on the SP app registration:

| Name | Subject | Purpose |
|------|---------|---------|
| `github-main` | `repo:neuralsignal/m365-extract:ref:refs/heads/main` | Dev deploys on merge |
| `github-env-dev` | `repo:neuralsignal/m365-extract:environment:dev` | Dev environment deployments |
| `github-env-prod` | `repo:neuralsignal/m365-extract:environment:prod` | Prod environment deployments |

**How they were created:**
```bash
# Replace APP_OBJECT_ID with the app registration object ID (d471c1e7-...)
APP_OBJECT_ID="d471c1e7-77bc-4b2b-a58c-e42ab4546bf9"

az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:neuralsignal/m365-extract:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Dev deploys on merge to main"
}'

az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters '{
  "name": "github-env-dev",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:neuralsignal/m365-extract:environment:dev",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Dev environment"
}'

az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters '{
  "name": "github-env-prod",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:neuralsignal/m365-extract:environment:prod",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Prod environment"
}'
```

**Gotcha:** The `--id` parameter takes the **app registration object ID** (not the SP object ID, not the app/client ID). Find it via `az ad app list --display-name "sp-m365-extract-deploy" --query '[].id' -o tsv`.

## Resource Provider Registration

All required providers registered on 2026-03-25:

| Provider | Status |
|----------|--------|
| Microsoft.Storage | Registered |
| Microsoft.ContainerRegistry | Registered |
| Microsoft.DBforPostgreSQL | Registered |
| Microsoft.Web | Registered |
| Microsoft.KeyVault | Registered |
| Microsoft.ContainerInstance | Registered |

**How to register (one-time, if not already done):**
```bash
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.Web
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.ContainerInstance

# Check status (may take a few minutes to go from Registering -> Registered)
az provider show --namespace Microsoft.ContainerInstance --query registrationState -o tsv
```

## GitHub Configuration

### Secrets (configured 2026-03-25)

| Secret | Value source | Purpose |
|--------|-------------|---------|
| `AZURE_CLIENT_ID` | SP app ID: `e31a8416-...` | OIDC login for GitHub Actions |
| `AZURE_TENANT_ID` | `ea0bd7d3-...` | Azure tenant |
| `AZURE_SUBSCRIPTION_ID` | `9f079696-...` | Target subscription |
| `ENTRA_CLIENT_ID` | Entra app ID: `f209d856-...` | User auth (passed to Bicep as `entraClientId`) |
| `AZURE_CLIENT_SECRET` | Entra app `reader` secret | User auth client secret |
| `POSTGRES_ADMIN_PASSWORD` | Chosen at setup | PostgreSQL admin password |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` | Session signing |
| `FERNET_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Token encryption |
| `ADMIN_EMAIL` | `matthias.christenson@sanoptis.com` | Admin user for UI |

**Important:** `AZURE_CLIENT_ID` (SP for deploy) and `ENTRA_CLIENT_ID` (app for user auth) are **different values**. The deploy workflow uses `AZURE_CLIENT_ID` for OIDC login and passes `ENTRA_CLIENT_ID` to Bicep for injecting into the app's config.

**How to set/update a secret:**
```bash
gh secret set SECRET_NAME --repo neuralsignal/m365-extract --body "value"
# Or interactively (for sensitive values):
gh secret set SECRET_NAME --repo neuralsignal/m365-extract
```

### Environments (configured 2026-03-25)

| Environment | Protection rules | Created via |
|-------------|-----------------|-------------|
| `dev` | None (auto-deploy on merge to main) | `gh api repos/neuralsignal/m365-extract/environments/dev -X PUT --input /dev/null` |
| `prod` | None yet (add manual approval later) | `gh api repos/neuralsignal/m365-extract/environments/prod -X PUT --input /dev/null` |

**Gotcha:** `gh api ... -X PUT -f wait_timer=0` fails with a type error -- the API expects an integer but `-f` sends strings. Use `--input /dev/null` for default settings, or `--input <(echo '{"wait_timer":0}')` for explicit values.

## Docker Images

| Image | Dockerfile | Purpose | Size |
|-------|-----------|---------|------|
| `m365-admin` | `Dockerfile.web` | Reflex admin UI (Caddy + backend) | ~457MB |
| `m365-daemon` | `Dockerfile.daemon` | Sync daemon | ~357MB |

### Architecture: Web Image

Based on the official Reflex `production-one-port` pattern:

```
Browser -> :8000 (Caddy) -> static files from /srv (frontend)
                         -> reverse proxy to :8001 (Reflex backend)
```

Caddy routes `/_event/*`, `/ping`, `/_upload/*` to the Python backend. Everything else serves from the pre-built React frontend.

**Why Caddy, not `reflex run --env prod` alone?**
- `reflex run --env prod` (without `--backend-only`) requires Node.js at runtime to serve the frontend
- The production-one-port pattern with Caddy eliminates Node.js from the runtime image
- Caddy handles gzip, static files, and reverse proxy efficiently
- Single port (8000) is required for Azure App Service (`WEBSITES_PORT`)

### Health checks

| Image | Check | Interval | Details |
|-------|-------|----------|---------|
| `m365-admin` | `curl -f http://localhost:8000/ping` | 30s, 3 retries | Hits Caddy -> backend |
| `m365-daemon` | `python scripts/daemon_healthcheck.py` | 60s, 3 retries | Reads `state/daemon_health.json`, fails if last cycle >5min ago |

### Build commands (local)

```bash
cd ~/Brain/external/m365-extract

# Build web image
docker build -t m365-admin:local -f Dockerfile.web .

# Build daemon image
docker build -t m365-daemon:local -f Dockerfile.daemon .

# Build base CLI image
docker build -t m365-extract:local .
```

## Local Testing

### Prerequisites

- `.env` file with all admin vars set (see `.env.example`)
- `state/` directory exists (auto-created)
- pixi admin environment installed: `pixi install -e admin`

### Start locally (pixi, SQLite)

```bash
# Terminal 1 -- Reflex admin UI
cd ~/Brain/external/m365-extract
pixi run -e admin dev
# -> http://localhost:3000

# Terminal 2 -- Sync daemon
cd ~/Brain/external/m365-extract
pixi run -e admin daemon
# Polls every config.service.continuous_poll_seconds (30s in config.web.yaml)
```

### Docker Compose (PostgreSQL, matching production)

```bash
# Full-stack (postgres + web + daemon)
docker compose up --build

# Just PostgreSQL (for local dev against pg)
docker compose up -d postgres

# Tear down (including volumes)
docker compose down -v
```

### Smoke test sequence

1. Open `http://localhost:3000` (pixi) or `http://localhost:8000` (Docker) -- should redirect to `/login`
2. Click login -- Entra OAuth flow -- callback -- `/dashboard`
3. `/settings` -- toggle extractors on/off
4. `/admin` -- see user table with your user, toggle enabled
5. Watch daemon terminal -- should pick up your user on next cycle
6. Refresh `/dashboard` -- should show SyncRecord with status + timestamp

### Verified working (2026-03-25)

- [x] `pixi run -e admin dev` starts without errors on port 3000/8000
- [x] Frontend HTTP 200 on `http://localhost:3000/`
- [x] Backend HTTP 200 on `http://localhost:8000/ping`
- [x] Daemon starts and syncs -- email (100 items), calendar (37 events), teams chats (28 chats)
- [x] `pixi run -e admin test-all` -- 383 tests pass
- [x] `docker build -f Dockerfile.web .` -- succeeds (457MB)
- [x] `docker build -f Dockerfile.daemon .` -- succeeds (357MB)

## Azure Infrastructure (Bicep)

Resource group: `rg-m365-extract-{env}`

### Resources defined in `infra/main.bicep`

| Resource | Azure Type | Naming | Purpose |
|----------|------------|--------|---------|
| Storage Account | `storageAccounts` | `stm365ext{env}` | Blob storage for vault output |
| Blob Container | `blobServices/containers` | `m365-vaults[-dev]` | Vault files |
| Container Registry | `registries` | `acrm365ext{env}` | Docker images for web + daemon |
| PostgreSQL Flexible | `flexibleServers` | `psql-m365-extract-{env}` | Shared DB (UI + daemon) |
| PostgreSQL Database | `databases` | `m365extract` | App database |
| App Service Plan | `serverfarms` | `asp-m365-extract-{env}` | Linux container hosting |
| App Service | `sites` | `app-m365-admin-{env}` | Reflex admin UI (system-assigned managed identity) |
| Container Instance | `containerGroups` | `ci-m365-daemon-{env}` | Sync daemon (always-on) |
| Key Vault | `vaults` | `kv-m365-ext-{env}` | Secrets (FERNET_KEY, SECRET_KEY, client_secret) |

### Environment parameters

| Parameter | Dev | Prod |
|-----------|-----|------|
| Storage SKU | Standard_LRS | Standard_GRS |
| ACR SKU | Basic | Standard |
| PostgreSQL SKU | Standard_B1ms (Burstable) | Standard_B2ms (Burstable) |
| PostgreSQL Storage | 32 GB | 64 GB |
| App Service Plan | B1 | P1v2 |

### Environment variables injected by Bicep

| Env Var | App Service | Container Instance | Secure? |
|---------|-------------|-------------------|---------|
| `DATABASE_URL` | Yes | Yes | Yes |
| `SECRET_KEY` | Yes | Yes | Yes |
| `FERNET_KEY` | Yes | Yes | Yes |
| `AZURE_CLIENT_ID` | Yes | Yes | No |
| `AZURE_TENANT_ID` | Yes | Yes | No |
| `AZURE_CLIENT_SECRET` | Yes | Yes | Yes |
| `M365_ADMIN_REDIRECT_URI` | Yes (derived from app hostname) | No | No |
| `M365_ADMIN_CONFIG` | Yes | Yes | No |
| `ADMIN_EMAIL` | Yes | Yes | No |
| `AZURE_STORAGE_CONNECTION_STRING` | No | Yes | Yes |
| `AZURE_STORAGE_CONTAINER` | No | Yes | No |
| `AZURE_STORAGE_PREFIX` | No | Yes | No |
| `WEBSITES_PORT` | Yes | No | No |

### Deploy infrastructure (manual)

```bash
# Set all required environment variables
export POSTGRES_ADMIN_PASSWORD="<strong-password>"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export AZURE_CLIENT_SECRET="<entra-client-secret>"
export AZURE_CLIENT_ID="f209d856-e14d-4dcf-87cc-bf0d98bb092b"
export AZURE_TENANT_ID="ea0bd7d3-b29f-47f4-aedc-da7b52a28ba0"
export ADMIN_EMAIL="matthias.christenson@sanoptis.com"

# Deploy dev
bash scripts/deploy-infra.sh dev

# Deploy prod
bash scripts/deploy-infra.sh prod

# Validate only (no deploy)
bash scripts/deploy-infra.sh dev --dry-run
```

The deploy script:
1. Validates `az` login and all required env vars
2. Creates resource group `rg-m365-extract-{env}`
3. Deploys all Bicep resources (idempotent)
4. Retrieves storage connection string
5. Writes `.env.{env}` with all connection details

**Note:** `AZURE_CLIENT_ID` in `deploy-infra.sh` refers to the **Entra app** (workflow-read, `f209d856-...`), not the deploy SP. The deploy script injects this into the app's config so users authenticate against the right Entra app.

### Build and push Docker images (manual)

```bash
# Build + push both images with unique tag (recommended)
bash scripts/build-and-push.sh dev --tag $(date +%Y%m%d%H%M%S)

# Custom tag
bash scripts/build-and-push.sh dev --tag v0.2.2

# Web only / Daemon only
bash scripts/build-and-push.sh dev --web-only
bash scripts/build-and-push.sh dev --daemon-only
```

### Key Vault RBAC (manual, post-deployment)

The Bicep template no longer assigns Key Vault roles (role assignment in Bicep requires Owner at deploy time, and the SP only has Contributor). After deployment, run manually:

```bash
# Get the App Service managed identity principal ID from deployment output
# (printed by deploy-infra.sh, or query it):
PRINCIPAL_ID=$(az webapp identity show --name app-m365-admin-dev \
  --resource-group rg-m365-extract-dev --query principalId -o tsv)

az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Key Vault Secrets User" \
  --scope "/subscriptions/9f079696-135d-4d18-a208-3e2e55fca2f5/resourceGroups/rg-m365-extract-dev/providers/Microsoft.KeyVault/vaults/kv-m365-ext-dev"
```

## CI/CD

### CI (`ci.yml`)

| Job | What it runs |
|-----|-------------|
| `lint` | `pixi run lint` + `pixi run format-check` |
| `test` | Unit tests + Azurite integration tests |
| `test-admin` | All tests including admin: `pixi run -e admin test-all` |

### CD (`deploy.yml`)

| Trigger | Environment | Image tag |
|---------|------------|-----------|
| Push to `main` (non-docs) | `dev` | Git SHA short (7 chars) |
| Tag push `v*` | `prod` | Tag name |
| Manual dispatch | User choice | User input or SHA |

Pipeline steps:
1. Build + push Docker images to ACR (unique tag per deploy)
2. Deploy Bicep infrastructure (idempotent)
3. Update App Service container image + restart
4. Restart daemon Container Instance
5. Smoke test (`/ping` endpoint, 30s wait)

---

## Gotchas and Learnings

Comprehensive list of gotchas discovered during the 2026-03-25 deployment session, organized by category.

### Docker / Reflex

1. **`README.md` must be in Docker context** -- `pyproject.toml` references `readme = "README.md"`, and hatchling fails metadata generation if the file is missing. The original `.dockerignore` excluded `README.md`. Fix: `!README.md` exception in `.dockerignore`.

2. **`scripts/` excluded by `.dockerignore`** -- The daemon healthcheck script lives in `scripts/`. Fix: `!scripts/daemon_healthcheck.py` exception in `.dockerignore`.

3. **`unzip` required for `reflex init`** -- Reflex's `reflex init` installs bun, which requires `unzip`. The `python:3.12-slim` image doesn't include it. Must install `unzip` in the builder stage.

4. **Reflex 0.8.x exports to `.web/build/client/`** -- Not `.web/_static/` as older versions did. Reflex now uses React Router (not Next.js). The production-compose example shows the correct cleanup pattern: `mv .web/build/client /tmp/client && rm -rf .web && mkdir -p .web/build && mv /tmp/client .web/build/client` -- this discards `node_modules` from the builder stage.

5. **Production pattern: Caddy + `--backend-only`** -- Based on Reflex's `production-one-port` example. No Node.js needed at runtime. Caddy serves static files and reverse-proxies to the Reflex backend. Single port (8000) is required for Azure App Service.

6. **`STOPSIGNAL SIGKILL` required** -- Per Reflex docs: "Needed until Reflex properly passes SIGTERM on backend." Must be in both Dockerfiles.

7. **Reflex creates `.states` directory at startup** -- `StateManagerDisk` writes state to `/app/.states/`. The `/app` dir is owned by root, so non-root `appuser` gets `PermissionError`. Fix: `mkdir -p /app/.states && chown appuser:appuser /app/.states` in the Dockerfile.

8. **Caddy data directories** -- Caddy needs writable dirs for TLS certs and config. Set `XDG_DATA_HOME=/data` and `XDG_CONFIG_HOME=/config` env vars, and `chown` those dirs to `appuser`.

9. **Caddy `file_server` strips query params on 308 directory redirect** -- When a browser requests `/callback?code=xxx&state=yyy`, Caddy's `file_server` sees no file at `/callback`, finds a directory-like match, and issues a 308 redirect to `/callback/` -- stripping the query parameters. The OAuth authorization code is lost and the login fails silently. Fix: use `try_files {path} {path}/index.html {path}/ /index.html` in the Caddyfile to serve `index.html` directly for SPA routes without triggering a directory redirect.

10. **`psycopg2-binary` must be in pyproject.toml** -- The PostgreSQL driver is not included by default. Add it to the `admin` extras in `pyproject.toml` or the app crashes at startup when `DATABASE_URL` points to PostgreSQL.

11. **`PYTHONPATH=/app` required in Docker images** -- The `m365_admin` package is copied to `/app/m365_admin/` but is not pip-installed as an editable package. Without `PYTHONPATH=/app`, the pip-installed entrypoint cannot find `m365_admin` imports. Set `ENV PYTHONPATH=/app` in the Dockerfile.

### ACI / Bicep

12. **ACI image caching -- `:latest` does not re-pull** -- `az deployment group create` resolves `:latest` to a specific image digest at the ARM template level. Subsequent pushes to `:latest` in ACR do **not** update running containers or cause re-pulls on redeployment. Fix: use unique tags (timestamp or git SHA) for every deploy. Never rely on `:latest` for ACI.

13. **ACI logs show "None" on crash-before-stdout** -- When an ACI container crashes before producing any stdout (e.g., import error, missing env var), `az container logs` returns "None" and stderr is lost. Fix: wrap the entrypoint with `sh -c 'command 2>&1'` and use `--restart-policy Never` to capture the error output instead of restart-looping.

14. **ACI deletion required before image change** -- Bicep's ARM deployment tries to update the Container Instance in-place, but ACI does not re-pull images on update. Fix: delete the container group before re-creating it with a new image, or use `az container create` with explicit image tag and registry credentials after the Bicep deploy.

15. **Resource providers must be registered before first Bicep deploy** -- If a provider (e.g., `Microsoft.ContainerInstance`) is not registered, the Bicep deployment fails with a cryptic error. Registration is async and may take several minutes.

16. **Push images BEFORE first Bicep deploy** -- ACI resources in Bicep reference container images in ACR. If the images do not exist yet, the deployment fails. On the very first deploy, manually create the ACR and push images before running Bicep.

### Azure Identity / Entra

17. **`az ad sp create-for-rbac` creates the SP even when role assignment fails** -- The command exits non-zero if role assignment fails (e.g., caller is only Contributor, not Owner), but the SP and app registration are still created. Always check with `az ad sp list --display-name "..."` after a failure.

18. **Federated credentials go on the app registration object ID** -- Not the SP object ID, not the client ID. The `--id` parameter in `az ad app federated-credential create` must be the app registration's object ID. Find it via: `az ad app list --display-name "sp-m365-extract-deploy" --query '[].id' -o tsv`.

19. **`az ad app update --web-redirect-uris` is a REPLACE operation** -- Not append. Omitting an existing URI from the command removes it. Always include all URIs (old and new) in every call.

20. **Client secret value shown only once** -- The Entra portal shows the secret name (e.g., "reader") but the actual secret value is only displayed at creation time. Store it immediately in a password manager or GitHub secret.

### GitHub Actions

21. **Two different client IDs in secrets** -- `AZURE_CLIENT_ID` is the deploy service principal (`e31a8416-...`) used for OIDC login. `ENTRA_CLIENT_ID` is the user auth app (`f209d856-...`) passed to Bicep. Mixing them up causes OIDC login to fail or users to authenticate against the wrong Entra app.

22. **`gh api` with `-f` sends strings, not integers** -- For GitHub environment creation, `gh api ... -f wait_timer=0` fails because the API expects an integer. Fix: use `--input /dev/null` for default settings, or pipe JSON: `--input <(echo '{"wait_timer":0}')`.

23. **OIDC federated credentials require app registration object ID** -- Same as gotcha #18, but from the GitHub Actions perspective: the federated identity is configured on the app registration, not the service principal.

### Caddy / OAuth Flow

24. **Caddy directory redirect breaks OAuth callback** -- This is gotcha #9 restated from the OAuth perspective. The OAuth authorization code flow sends the user back to `/callback?code=AUTH_CODE&state=STATE`. If Caddy redirects `/callback` to `/callback/` via 308, the query parameters (including the authorization code) are stripped. The Reflex `handle_callback()` handler sees no code and the login fails. The `try_files` directive is the correct fix -- it serves the SPA's `index.html` for all routes without triggering directory redirects.

---

### Application / PostgreSQL

25. **Token storage before user creation causes FK violation** -- `auth_state.py:_persist_user_and_tokens()` originally called `store_tokens()` before creating the `User` row. The `tokenrecord` table has a foreign key to `user`. SQLite doesn't enforce FKs by default, so this worked locally. PostgreSQL enforces FKs strictly, causing `IntegrityError: ForeignKeyViolation`. Fix: create the user FIRST, then store tokens. Symptom: login button spins, then shows "Contact the website administrator" toast.

26. **"Contact the website administrator" may not be from Entra** -- This error message appears as a Reflex toast when the `handle_callback()` handler throws an unhandled exception (like the FK violation above). It looks like an Entra error but it's actually a backend crash. Check App Service container logs (`_default_docker.log`) for the real traceback.

### Storage / Daemon

27. **Daemon `Permission denied: '/app/vault'` -- use Azure Blob in production** -- The daemon Dockerfile originally only created `/app/state/`. With `config.web.yaml` (local storage backend), the daemon tried to write to `/app/vault/` which didn't exist or wasn't owned by `appuser`. Fix: (1) use `config.deploy.yaml` in production with `storage.backend: "azure_blob"` so vault writes go to Azure Blob Storage, and (2) `mkdir -p /app/vault` in the Dockerfile as a fallback for any local state that might be written.

28. **`Permissions-Policy: unload` console warning** -- Chrome shows `Permissions policy violation: unload is not allowed in this document` because Reflex's socket.io registers a deprecated `unload` event listener. Not a bug, just a deprecation warning. Fix: add `header Permissions-Policy "unload=self"` to the Caddyfile, which tells Chrome the site is allowed to use the `unload` event.

29. **"A listener indicated an asynchronous response" console error** -- This is from a browser extension (password manager, ad blocker, etc.) using `chrome.runtime.onMessage`, not from Reflex or our code. Confirm by testing in Incognito mode (extensions disabled). No code change needed.

### Config Split

30. **Two config files: `config.web.yaml` vs `config.deploy.yaml`** -- `config.web.yaml` uses `storage.backend: "local"` (no blob env vars needed). `config.deploy.yaml` uses `storage.backend: "azure_blob"` with `${AZURE_STORAGE_CONNECTION_STRING}` etc. The config loader eagerly expands ALL `${VAR}` references at load time — even sections the caller doesn't use. So the **web app must use `config.web.yaml`** (it doesn't have storage env vars and doesn't need them). Only the **daemon uses `config.deploy.yaml`** (it has the storage env vars via Bicep). Both Dockerfiles copy both config files. Bicep sets `M365_ADMIN_CONFIG=./config.web.yaml` for App Service and `M365_ADMIN_CONFIG=./config.deploy.yaml` for Container Instance.

---

## Production Checklist

### Phase A -- Minimum viable deployment (2026-03-25)

- [x] Docker images build successfully (`Dockerfile.web` 457MB, `Dockerfile.daemon` 357MB)
- [x] `docker-compose.yaml` for full-stack local testing with PostgreSQL
- [x] Bicep template compiles with all env vars (15 App Service settings, 10 Container Instance vars)
- [x] `deploy.yml` triggers on push to main (-> dev) and tag push (-> prod)
- [x] Daemon health check reads `state/daemon_health.json` (written after each cycle)
- [x] Service principal `sp-m365-extract-deploy` created (`e31a8416-...`)
- [x] SP Contributor role assigned on subscription
- [x] 3 OIDC federated credentials (main, dev env, prod env)
- [x] All 9 GitHub secrets configured
- [x] GitHub environments created (dev, prod)
- [x] Production redirect URI added to Entra app
- [x] 383 tests pass
- [x] First deployment: Bicep deployed all 7 resources to `rg-m365-extract-dev`
- [x] Images pushed to ACR (`acrm365extdev.azurecr.io`)
- [x] Smoke test: `https://app-m365-admin-dev.azurewebsites.net/ping` returns 200
- [x] Frontend: `https://app-m365-admin-dev.azurewebsites.net/` returns 200
- [x] Daemon Container Instance state: Running
- [ ] OAuth login works on Azure (production redirect URI)
- [ ] Daemon syncs with PostgreSQL (verify in `/dashboard`)
- [ ] Key Vault RBAC assigned to App Service managed identity

### Phase B -- Production hardening

- [ ] Managed Identity -> Key Vault access (resolve RBAC limitation)
- [ ] VNet + private endpoints for PostgreSQL and Storage
- [ ] Custom domain + TLS cert on App Service
- [ ] Log Analytics workspace for container logs
- [ ] Prod environment: add required reviewer protection rule
