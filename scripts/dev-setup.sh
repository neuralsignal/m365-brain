#!/usr/bin/env bash
# Idempotent dev environment setup for m365-extract.
#
# What it does:
#   1. Checks that Docker and pixi are available
#   2. Installs pixi environment (idempotent)
#   3. Starts Azurite emulator (idempotent — docker compose up -d is a no-op if already running)
#   4. Waits for Azurite blob service to be reachable
#   5. Writes .env.dev if missing (Azurite well-known credentials)
#
# Usage:
#   bash scripts/dev-setup.sh
#
# Then run tests separately:
#   pixi run test            # unit tests
#   pixi run test-azurite    # azurite integration tests
#
# Safe to re-run at any time.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# -- Colors for output -------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# -- 1. Check prerequisites ---------------------------------------------------

command -v docker >/dev/null 2>&1 || fail "docker not found. Install Docker Desktop or docker-ce."
docker info >/dev/null 2>&1 || fail "Docker daemon not running. Start Docker first."
info "Docker is available"

command -v pixi >/dev/null 2>&1 || fail "pixi not found. Install from https://pixi.sh"
info "pixi is available"

# -- 2. Install pixi environment (idempotent) ---------------------------------

pixi install --quiet 2>/dev/null || pixi install
info "pixi environment installed"

# -- 3. Start Azurite ---------------------------------------------------------

echo ""
echo "Starting Azurite emulator..."

docker compose --profile azurite up -d 2>/dev/null
info "Azurite container started (or already running)"

# Wait for blob service to be reachable
MAX_WAIT=30
WAITED=0
while ! bash -c 'echo > /dev/tcp/127.0.0.1/10000' 2>/dev/null; do
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        fail "Azurite not reachable on localhost:10000 after ${MAX_WAIT}s"
    fi
    sleep 1
done
info "Azurite blob service reachable on localhost:10000"

# -- 4. Ensure .env.dev exists ------------------------------------------------

ENV_DEV="$PROJECT_DIR/.env.dev"
if [ ! -f "$ENV_DEV" ]; then
    cat > "$ENV_DEV" << 'ENVEOF'
# Azurite emulator — well-known dev credentials (not a secret)
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
AZURE_STORAGE_CONTAINER=m365-vaults-dev
AZURE_STORAGE_PREFIX=local-dev/
ENVEOF
    info "Created .env.dev with Azurite credentials"
else
    info ".env.dev already exists"
fi

# -- Done ---------------------------------------------------------------------

echo ""
echo -e "${GREEN}Dev environment ready.${NC}"
echo ""
echo "Run tests:"
echo "  pixi run test            # unit tests (no Azurite needed)"
echo "  pixi run test-azurite    # integration tests against Azurite"
echo ""
echo "Smoke test sync:"
echo "  source .env.dev"
echo "  pixi run m365-extract --config config.azure.yaml sync --once --extractors email"
echo ""
echo "Teardown:"
echo "  bash scripts/teardown-dev.sh"
