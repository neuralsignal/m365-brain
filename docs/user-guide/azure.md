# Azure Setup

This guide covers deploying m365-extract with Azure Blob Storage, including local development with Azurite and production deployment with Bicep IaC.

## Azure Configuration

Use composable config fragments to include the Azure Blob storage backend:

```bash
m365-extract --config config/base.yaml,config/auth.yaml,config/storage/azure_blob.yaml,config/service/cli.yaml sync --once
```

The storage fragment (`config/storage/azure_blob.yaml`):

```yaml
storage:
  backend: "azure_blob"
  azure_blob:
    connection_string: "${AZURE_STORAGE_CONNECTION_STRING}"
    container_name: "${AZURE_STORAGE_CONTAINER}"
    prefix: "${AZURE_STORAGE_PREFIX}"
```

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `AZURE_STORAGE_CONNECTION_STRING` | Storage account connection string | `DefaultEndpointsProtocol=https;AccountName=...` |
| `AZURE_STORAGE_CONTAINER` | Blob container name | `m365-vaults` |
| `AZURE_STORAGE_PREFIX` | Per-user prefix within the container | `user1/` |

## Local Development with Azurite

[Azurite](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite) is the official Azure Storage emulator. Use it for local development and running integration tests without an Azure subscription.

### Start Azurite

Using the provided Docker Compose file:

```bash
docker compose --profile azurite up -d
```

This starts Azurite with all three services (Blob, Queue, Table) on their default ports:

| Service | Port |
|---------|------|
| Blob | 10000 |
| Queue | 10001 |
| Table | 10002 |

### Azurite Environment Variables

Set these in your `.env` file for local development:

```env
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
AZURE_STORAGE_CONTAINER=m365-vaults-dev
AZURE_STORAGE_PREFIX=local/
```

!!! note
    The Azurite connection string above uses the well-known development account credentials. These are not secret and are the same for all Azurite instances.

### Running Azurite Integration Tests

With Azurite running:

```bash
pixi run test-azurite
```

## Bicep IaC Deployment

The `infra/` directory contains Bicep templates for deploying the full Azure infrastructure.

### Template: `infra/main.bicep`

Creates:

- **Storage Account** (`StorageV2`, TLS 1.2, HTTPS-only, no public blob access)
- **Blob Container** (for storing synced markdown vaults)
- **Azure Container Registry** (Docker images for web + daemon)
- **PostgreSQL Flexible Server** (v16, Burstable B1ms, 32GB)
- **Key Vault** (RBAC-enabled, soft delete)
- **App Service Plan** (Linux, B1)
- **App Service** (Reflex admin UI, system-managed identity)
- **Container Instance** (sync daemon, always restart)
- **Log Analytics Workspace** (PerGB2018 pricing, 30d dev / 90d prod retention)
- **Diagnostic Settings** (App Service + PostgreSQL logs and metrics → Log Analytics)

### Prod Deployment Checklist

See `roadmap.md` Phase 5E for the full step-by-step checklist. Summary:

1. Add prod redirect URI to Entra app registration (replaces, not appends)
2. Create `prod` GitHub environment with separate secrets
3. Tag and push: `git tag v0.3.0 && git push origin v0.3.0`
4. Verify OAuth login, daemon logs, sync history, Log Analytics

### Deploy: Development

```bash
az deployment group create \
  --resource-group rg-m365-extract-dev \
  --template-file infra/main.bicep \
  --parameters infra/params.dev.bicepparam
```

Development parameters (`infra/params.dev.bicepparam`):

- Environment: `dev`
- SKU: `Standard_LRS` (locally redundant, lower cost)
- Container: `m365-vaults-dev`

### Deploy: Production

```bash
az deployment group create \
  --resource-group rg-m365-extract-prod \
  --template-file infra/main.bicep \
  --parameters infra/params.prod.bicepparam
```

Production parameters (`infra/params.prod.bicepparam`):

- Environment: `prod`
- SKU: `Standard_GRS` (geo-redundant for durability)
- Container: `m365-vaults`

### Post-Deployment

After deployment, retrieve the connection string:

```bash
az storage account show-connection-string \
  --resource-group rg-m365-extract-prod \
  --name stm365extprod \
  --output tsv
```

Set it as the `AZURE_STORAGE_CONNECTION_STRING` environment variable.

## Storage Account Naming

The Bicep template generates storage account names as `stm365ext{environment}`:

| Environment | Storage Account Name |
|------------|---------------------|
| `dev` | `stm365extdev` |
| `prod` | `stm365extprod` |

Azure storage account names must be globally unique, 3-24 characters, lowercase alphanumeric only.
