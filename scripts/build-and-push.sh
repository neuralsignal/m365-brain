#!/usr/bin/env bash
# Build Docker images and push to Azure Container Registry.
#
# Usage:
#   bash scripts/build-and-push.sh dev              # build + push both images
#   bash scripts/build-and-push.sh dev --web-only   # build + push web image only
#   bash scripts/build-and-push.sh dev --daemon-only  # build + push daemon image only
#   bash scripts/build-and-push.sh dev --tag v1.2.3  # custom tag (default: latest)
#
# Prerequisites:
#   - Docker running
#   - az CLI logged in
#   - ACR exists (run deploy-infra.sh first)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# -- Parse arguments ----------------------------------------------------------

ENV=""
TAG="latest"
BUILD_WEB=true
BUILD_DAEMON=true

while [ $# -gt 0 ]; do
    case "$1" in
        dev|prod|test) ENV="$1" ;;
        --tag) shift; TAG="$1" ;;
        --web-only) BUILD_DAEMON=false ;;
        --daemon-only) BUILD_WEB=false ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$ENV" ]; then
    echo "Usage: $0 <dev|prod|test> [--tag TAG] [--web-only|--daemon-only]"
    exit 1
fi

# -- Colors -------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# -- Prerequisites ------------------------------------------------------------

command -v docker >/dev/null 2>&1 || fail "docker not found"
command -v az >/dev/null 2>&1 || fail "az CLI not found"

# Resolve ACR name from deployment or env file
ACR_NAME="${ACR_NAME:-acrm365ext${ENV}}"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}"

# Check if .env.{env} exists and source ACR vars
ENV_FILE="$PROJECT_DIR/.env.${ENV}"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}"
fi

info "ACR: $ACR_LOGIN_SERVER"
info "Tag: $TAG"

# -- Login to ACR -------------------------------------------------------------

echo ""
echo "Logging in to ACR..."
az acr login --name "$ACR_NAME"
info "ACR login successful"

# -- Build + Push Web Image ---------------------------------------------------

if [ "$BUILD_WEB" = true ]; then
    echo ""
    echo "Building web image (Dockerfile.web)..."
    WEB_IMAGE="${ACR_LOGIN_SERVER}/m365-admin:${TAG}"

    docker build \
        -t "$WEB_IMAGE" \
        -f Dockerfile.web \
        .

    info "Built: $WEB_IMAGE"

    echo "Pushing web image..."
    docker push "$WEB_IMAGE"
    info "Pushed: $WEB_IMAGE"
fi

# -- Build + Push Daemon Image ------------------------------------------------

if [ "$BUILD_DAEMON" = true ]; then
    echo ""
    echo "Building daemon image (Dockerfile.daemon)..."
    DAEMON_IMAGE="${ACR_LOGIN_SERVER}/m365-daemon:${TAG}"

    docker build \
        -t "$DAEMON_IMAGE" \
        -f Dockerfile.daemon \
        .

    info "Built: $DAEMON_IMAGE"

    echo "Pushing daemon image..."
    docker push "$DAEMON_IMAGE"
    info "Pushed: $DAEMON_IMAGE"
fi

# -- Done ---------------------------------------------------------------------

echo ""
echo -e "${GREEN}Build and push complete.${NC}"
echo ""
echo "Next steps:"
echo "  # Update App Service to new image:"
echo "  az webapp config container set --name app-m365-admin-${ENV} --resource-group rg-m365-extract-${ENV} --container-image-name ${ACR_LOGIN_SERVER}/m365-admin:${TAG}"
echo ""
echo "  # Restart daemon container:"
echo "  az container restart --name ci-m365-daemon-${ENV} --resource-group rg-m365-extract-${ENV}"
