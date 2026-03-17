# Docker

m365-extract includes a multi-stage Dockerfile and a Docker Compose file for local development with Azurite.

## Dockerfile

The Dockerfile uses a multi-stage build for a small final image:

### Stage 1: Builder

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY m365_extract/ m365_extract/
RUN pip install --no-cache-dir ".[azure]"
```

Installs the package with the `azure` extra (Azure Blob Storage support).

### Stage 2: Runtime

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/m365-extract /usr/local/bin/m365-extract
COPY m365_extract/ m365_extract/
RUN useradd --create-home appuser
USER appuser
ENTRYPOINT ["m365-extract"]
```

- Runs as a non-root user (`appuser`)
- Only copies installed packages and the CLI entrypoint from the builder
- Entrypoint is the `m365-extract` CLI

### Build

```bash
docker build -t m365-extract .
```

### Run

```bash
# One-time sync
docker run --rm \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/state:/app/state \
  -v $(pwd)/vault:/app/vault \
  -e MSAL_CLIENT_ID \
  -e MSAL_TENANT_ID \
  m365-extract --config /app/config.yaml sync --once

# Continuous sync
docker run -d \
  --name m365-extract \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/state:/app/state \
  -v $(pwd)/vault:/app/vault \
  -e MSAL_CLIENT_ID \
  -e MSAL_TENANT_ID \
  m365-extract --config /app/config.yaml sync --continuous
```

!!! warning "Token cache"
    Mount the `state/` directory to persist the MSAL token cache across container restarts. Without this, you will need to re-authenticate via device code flow every time the container starts.

### Volume Mounts

| Mount | Purpose |
|-------|---------|
| `config.yaml` | Configuration file (read-only) |
| `state/` | Token cache and sync state persistence |
| `vault/` | Output directory for local backend (not needed for Azure Blob) |

### Environment Variables

Pass all required environment variables via `-e` flags or an env file:

```bash
docker run --rm --env-file .env \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/state:/app/state \
  m365-extract --config /app/config.yaml sync --once
```

## Docker Compose (Development)

The `docker-compose.dev.yaml` file provides an Azurite emulator for local Azure Blob Storage development:

```yaml
services:
  azurite:
    image: mcr.microsoft.com/azure-storage/azurite:latest
    ports:
      - "10000:10000"
      - "10001:10001"
      - "10002:10002"
    command: >-
      azurite
      --blobHost 0.0.0.0
      --queueHost 0.0.0.0
      --tableHost 0.0.0.0
      --skipApiVersionCheck
```

### Start Azurite

```bash
docker compose -f docker-compose.dev.yaml up -d
```

### Connect to Azurite

Use the well-known Azurite development credentials in your `.env`:

```env
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
AZURE_STORAGE_CONTAINER=m365-vaults-dev
AZURE_STORAGE_PREFIX=local/
```

### Stop Azurite

```bash
docker compose -f docker-compose.dev.yaml down
```

Azurite data is ephemeral by default. Add a volume mount if you need persistence:

```yaml
services:
  azurite:
    volumes:
      - azurite-data:/data
volumes:
  azurite-data:
```
