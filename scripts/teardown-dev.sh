#!/usr/bin/env bash
# Stop Azurite and clean up dev resources. Safe to re-run.
#
# Usage:
#   bash scripts/teardown-dev.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[OK]${NC} $*"; }

# Stop Azurite
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker compose -f docker-compose.dev.yaml down 2>/dev/null && \
        info "Azurite stopped" || \
        info "Azurite was not running"
else
    info "Docker not available — nothing to stop"
fi
