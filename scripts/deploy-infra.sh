#!/usr/bin/env bash
# Idempotent Azure infrastructure deployment for m365-extract.
#
# What it does:
#   1. Checks that az CLI is logged in
#   2. Creates the resource group if it doesn't exist
#   3. Deploys Bicep template (idempotent — ARM deployments are declarative)
#   4. Retrieves outputs (storage connection string, ACR login server, etc.)
#   5. Writes .env.{env} with connection details
#
# Usage:
#   bash scripts/deploy-infra.sh dev                    # deploy all infra
#   bash scripts/deploy-infra.sh prod                   # deploy prod infra
#   bash scripts/deploy-infra.sh dev --dry-run          # validate without deploying
#   bash scripts/deploy-infra.sh dev --component storage  # deploy storage only (original template)
#
# Environment variables:
#   POSTGRES_ADMIN_PASSWORD  — required (prompted if not set)
#
# Safe to re-run at any time — all operations are idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# -- Parse arguments ----------------------------------------------------------

ENV=""
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        dev|prod|test) ENV="$arg" ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 <dev|prod|test> [--dry-run]"; exit 1 ;;
    esac
done

if [ -z "$ENV" ]; then
    echo "Usage: $0 <dev|prod|test> [--dry-run]"
    exit 1
fi

LOCATION="switzerlandnorth"
RESOURCE_GROUP="rg-m365-extract-${ENV}"
PARAMS_FILE="infra/params.${ENV}.json"
TEMPLATE_FILE="infra/main.bicep"

# -- Colors -------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# -- 1. Check prerequisites ---------------------------------------------------

command -v az >/dev/null 2>&1 || fail "az CLI not found. Install: https://learn.microsoft.com/cli/azure/install-azure-cli"
info "az CLI is available"

# Check login status
if ! az account show >/dev/null 2>&1; then
    fail "Not logged in to Azure. Run: az login"
fi
ACCOUNT_NAME=$(az account show --query name -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
info "Logged in to Azure: $ACCOUNT_NAME ($SUBSCRIPTION_ID)"

# Check template + params files exist
[ -f "$TEMPLATE_FILE" ] || fail "Bicep template not found: $TEMPLATE_FILE"
[ -f "$PARAMS_FILE" ] || fail "Parameter file not found: $PARAMS_FILE"
info "Bicep template and params file found"

# -- 2. Collect secrets -------------------------------------------------------

if [ -z "${POSTGRES_ADMIN_PASSWORD:-}" ]; then
    echo ""
    read -sp "PostgreSQL admin password (POSTGRES_ADMIN_PASSWORD): " POSTGRES_ADMIN_PASSWORD
    echo ""
    if [ -z "$POSTGRES_ADMIN_PASSWORD" ]; then
        fail "PostgreSQL admin password is required"
    fi
fi

# App secrets — required for Bicep deployment
# Note: AZURE_CLIENT_ID here is the Entra app (workflow-read), not the deploy SP
for VAR in SECRET_KEY FERNET_KEY AZURE_CLIENT_SECRET AZURE_CLIENT_ID AZURE_TENANT_ID ADMIN_EMAIL; do
    if [ -z "${!VAR:-}" ]; then
        fail "$VAR is required. Set it as an environment variable before running this script."
    fi
done
info "All required secrets and config vars are set"

# -- 3. Dry run (validate only) -----------------------------------------------

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "Validating deployment (dry run)..."

    # Ensure resource group exists for validation
    if ! az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
        echo "  Resource group '$RESOURCE_GROUP' does not exist — creating for validation..."
        az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
    fi

    az deployment group validate \
        --resource-group "$RESOURCE_GROUP" \
        --template-file "$TEMPLATE_FILE" \
        --parameters "$PARAMS_FILE" \
        --parameters \
          postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
          secretKey="$SECRET_KEY" \
          fernetKey="$FERNET_KEY" \
          entraClientSecret="$AZURE_CLIENT_SECRET" \
          entraClientId="$AZURE_CLIENT_ID" \
          entraTenantId="$AZURE_TENANT_ID" \
          adminEmail="$ADMIN_EMAIL" \
        --output table

    info "Validation passed (no resources deployed)"
    exit 0
fi

# -- 4. Create resource group (idempotent) ------------------------------------

echo ""
echo "Ensuring resource group '$RESOURCE_GROUP' exists..."

if az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
    info "Resource group '$RESOURCE_GROUP' already exists"
else
    az group create \
        --name "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --output none
    info "Created resource group '$RESOURCE_GROUP' in $LOCATION"
fi

# -- 5. Deploy Bicep template (idempotent) ------------------------------------

echo ""
echo "Deploying Bicep template to '$RESOURCE_GROUP'..."
echo "  This deploys: Storage, ACR, PostgreSQL, App Service, Container Instance, Key Vault"
echo ""

DEPLOY_OUTPUT=$(az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$TEMPLATE_FILE" \
    --parameters "$PARAMS_FILE" \
    --parameters \
      postgresAdminPassword="$POSTGRES_ADMIN_PASSWORD" \
      secretKey="$SECRET_KEY" \
      fernetKey="$FERNET_KEY" \
      entraClientSecret="$AZURE_CLIENT_SECRET" \
      entraClientId="$AZURE_CLIENT_ID" \
      entraTenantId="$AZURE_TENANT_ID" \
      adminEmail="$ADMIN_EMAIL" \
    --output json)

# Extract outputs
STORAGE_ACCOUNT=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['storageAccountName']['value'])")
CONTAINER_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['containerName']['value'])")
ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['acrLoginServer']['value'])")
ACR_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['acrName']['value'])")
POSTGRES_HOST=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['postgresHost']['value'])")
POSTGRES_DB=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['postgresDbName']['value'])")
WEB_APP_URL=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['webAppUrl']['value'])")
WEB_APP_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['webAppName']['value'])")
KEY_VAULT_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['keyVaultName']['value'])")
KEY_VAULT_URI=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['properties']['outputs']['keyVaultUri']['value'])")

info "Deployed successfully"
echo "  Storage:    $STORAGE_ACCOUNT / $CONTAINER_NAME"
echo "  ACR:        $ACR_LOGIN_SERVER"
echo "  PostgreSQL: $POSTGRES_HOST / $POSTGRES_DB"
echo "  Web App:    $WEB_APP_URL"
echo "  Key Vault:  $KEY_VAULT_NAME"

# -- 6. Retrieve storage connection string ------------------------------------

echo ""
echo "Retrieving storage connection string..."

CONNECTION_STRING=$(az storage account show-connection-string \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --output tsv)

info "Connection string retrieved"

# -- 7. Write .env.{env} file ------------------------------------------------

ENV_FILE="$PROJECT_DIR/.env.${ENV}"

# URL-encode the password in case it contains special chars
ENCODED_PASSWORD=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$POSTGRES_ADMIN_PASSWORD")
PG_USER="${POSTGRES_ADMIN_USER:-m365admin}"

cat > "$ENV_FILE" << ENVEOF
# Auto-generated by deploy-infra.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Resource group: $RESOURCE_GROUP

# Storage
AZURE_STORAGE_CONNECTION_STRING=${CONNECTION_STRING}
AZURE_STORAGE_CONTAINER=${CONTAINER_NAME}
AZURE_STORAGE_PREFIX=${ENV}/

# Container Registry
ACR_LOGIN_SERVER=${ACR_LOGIN_SERVER}
ACR_NAME=${ACR_NAME}

# PostgreSQL
DATABASE_URL=postgresql://${PG_USER}:${ENCODED_PASSWORD}@${POSTGRES_HOST}:5432/${POSTGRES_DB}?sslmode=require
POSTGRES_HOST=${POSTGRES_HOST}

# Web App
WEB_APP_URL=${WEB_APP_URL}
WEB_APP_NAME=${WEB_APP_NAME}

# Key Vault
KEY_VAULT_NAME=${KEY_VAULT_NAME}
KEY_VAULT_URI=${KEY_VAULT_URI}
ENVEOF

info "Written $ENV_FILE"

# -- Done ---------------------------------------------------------------------

echo ""
echo -e "${GREEN}Infrastructure deployed successfully.${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  # Build + push Docker images:"
echo "  bash scripts/build-and-push.sh ${ENV}"
echo ""
echo "  # Store secrets in Key Vault:"
echo "  az keyvault secret set --vault-name $KEY_VAULT_NAME --name SECRET-KEY --value \"\$(python3 -c 'import secrets; print(secrets.token_hex(32))')\""
echo "  az keyvault secret set --vault-name $KEY_VAULT_NAME --name FERNET-KEY --value \"\$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')\""
echo ""
echo "  # Open the admin UI:"
echo "  echo $WEB_APP_URL"
