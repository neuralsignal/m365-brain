# Docker

m365-extract provides a single `Dockerfile` and `docker-compose.yaml` for local development. The sync daemon runs as a background thread inside the Reflex web app — no separate container needed.

## Docker Compose

The `docker-compose.yaml` runs the full stack locally: PostgreSQL and the Reflex admin UI + sync daemon (`Dockerfile`). An Azurite blob emulator is available via the `azurite` profile.

### Start full stack

```bash
docker compose up --build        # build + start all services
docker compose up -d postgres    # start only postgres (for local dev against pg)
docker compose --profile azurite up  # include Azurite blob emulator
docker compose down -v           # stop + remove volumes
```

### Azurite (local Azure Blob Storage)

Start Azurite via the profile:

```bash
docker compose --profile azurite up -d
```

Use the well-known Azurite development credentials in your `.env`:

```env
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
AZURE_STORAGE_CONTAINER=m365-vaults-dev
AZURE_STORAGE_PREFIX=local/
```

### Stop Azurite

```bash
docker compose --profile azurite down
```

Azurite data is ephemeral by default. Add a volume mount if you need persistence.
