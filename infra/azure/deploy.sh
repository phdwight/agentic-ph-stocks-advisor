#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — Deploy PH Stocks Advisor to Azure Container Apps
#
# Prerequisites:
#   1. Azure CLI installed     — https://aka.ms/install-az-cli
#   2. Logged in               — az login
#   3. Docker running          — docker info
#   4. A .env file (or export) with OPENAI_API_KEY (and optionally TAVILY_API_KEY)
#
# Usage:
#   ./infra/azure/deploy.sh                    # first-time full deploy
#   ./infra/azure/deploy.sh --update           # rebuild image & redeploy apps only
#   ./infra/azure/deploy.sh --infra-only       # provision Azure resources only (no image push)
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Defaults (override via environment) ──────────────────────────────────────
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-ph-stocks-advisor-rg}"
LOCATION="${AZURE_LOCATION:-southeastasia}"
APP_NAME="${AZURE_APP_NAME:-phstocks}"
PG_ADMIN_USER="${AZURE_PG_ADMIN_USER:-phadmin}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# ── Load .env if present ─────────────────────────────────────────────────────
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  echo "📄 Loading .env file …"
  set -a
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/.env"
  set +a
fi

# ── Parse flags ──────────────────────────────────────────────────────────────
UPDATE_ONLY=false
INFRA_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --update)     UPDATE_ONLY=true ;;
    --infra-only) INFRA_ONLY=true ;;
    *)            echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Require secrets ──────────────────────────────────────────────────────────
: "${OPENAI_API_KEY:?❌ OPENAI_API_KEY is required. Set it in .env or export it.}"
if [[ "$UPDATE_ONLY" == false ]]; then
  : "${AZURE_PG_PASSWORD:?❌ AZURE_PG_PASSWORD is required. Export it before running this script.}"
fi
TAVILY_API_KEY="${TAVILY_API_KEY:-}"
LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-}"
LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-}"
LANGFUSE_HOST="${LANGFUSE_HOST:-https://cloud.langfuse.com}"
LANGFUSE_ENVIRONMENT="${LANGFUSE_TRACING_ENVIRONMENT:-production}"
ENTRA_CLIENT_ID="${ENTRA_CLIENT_ID:-}"
ENTRA_CLIENT_SECRET="${ENTRA_CLIENT_SECRET:-}"
ENTRA_TENANT_ID="${ENTRA_TENANT_ID:-common}"
FLASK_SECRET_KEY="${FLASK_SECRET_KEY:-ph-stocks-advisor-change-me-in-production}"
ADMIN_SECRET_KEY="${ADMIN_SECRET_KEY:-sqladmin-change-me-in-production}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}"

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo -e "\n\033[1;34m▸ $*\033[0m"; }
ok()    { echo -e "\033[1;32m✔ $*\033[0m"; }
warn()  { echo -e "\033[1;33m⚠ $*\033[0m"; }

# ── 1. Ensure resource group exists ─────────────────────────────────────────
if [[ "$UPDATE_ONLY" == false ]]; then
  info "Creating resource group '$RESOURCE_GROUP' in '$LOCATION' …"
  az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none
  ok "Resource group ready."
fi

# ── 2. Deploy Bicep template ────────────────────────────────────────────────
if [[ "$UPDATE_ONLY" == false ]]; then
  info "Deploying Azure infrastructure via Bicep …"
  DEPLOY_OUTPUT=$(az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --template-file "$SCRIPT_DIR/main.bicep" \
    --parameters \
      appName="$APP_NAME" \
      location="$LOCATION" \
      pgAdminUser="$PG_ADMIN_USER" \
      pgAdminPassword="$AZURE_PG_PASSWORD" \
      openaiApiKey="$OPENAI_API_KEY" \
      tavilyApiKey="$TAVILY_API_KEY" \
      openaiModel="${OPENAI_MODEL:-gpt-4o-mini}" \
      langfusePublicKey="$LANGFUSE_PUBLIC_KEY" \
      langfuseSecretKey="$LANGFUSE_SECRET_KEY" \
      langfuseHost="$LANGFUSE_HOST" \
      langfuseEnvironment="$LANGFUSE_ENVIRONMENT" \
      entraClientId="$ENTRA_CLIENT_ID" \
      entraClientSecret="$ENTRA_CLIENT_SECRET" \
      entraTenantId="$ENTRA_TENANT_ID" \
      flaskSecretKey="$FLASK_SECRET_KEY" \
      googleClientId="$GOOGLE_CLIENT_ID" \
      googleClientSecret="$GOOGLE_CLIENT_SECRET" \
      adminSecretKey="$ADMIN_SECRET_KEY" \
      imageTag="$IMAGE_TAG" \
    --query properties.outputs \
    --output json)

  ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['acrLoginServer']['value'])")
  ACR_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['acrName']['value'])")
  WEB_URL=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['webAppUrl']['value'])")
  ADMIN_URL=$(echo "$DEPLOY_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['adminAppUrl']['value'])")

  ok "Infrastructure provisioned."
  echo "   ACR:     $ACR_LOGIN_SERVER"
  echo "   Web URL: $WEB_URL"
else
  # Fetch existing ACR details (for --update)
  info "Fetching existing ACR details …"
  ACR_NAME=$(az acr list --resource-group "$RESOURCE_GROUP" --query "[0].name" --output tsv)
  ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer --output tsv)
  ok "ACR: $ACR_LOGIN_SERVER"
fi

if [[ "$INFRA_ONLY" == true ]]; then
  ok "Infrastructure-only deploy complete. Run with --update to push the image."
  exit 0
fi

# ── 3. Build & push Docker image to ACR ─────────────────────────────────────
info "Logging into ACR '$ACR_NAME' …"
az acr login --name "$ACR_NAME"

IMAGE_FULL="${ACR_LOGIN_SERVER}/ph-stocks-advisor:${IMAGE_TAG}"
ADMIN_IMAGE_FULL="${ACR_LOGIN_SERVER}/ph-stocks-advisor-admin:${IMAGE_TAG}"

info "Building Docker image (linux/amd64) …"
docker build \
  --platform linux/amd64 \
  -t "$IMAGE_FULL" \
  -f "$PROJECT_ROOT/Dockerfile" \
  "$PROJECT_ROOT"

info "Building admin Docker image (linux/amd64) …"
docker build \
  --platform linux/amd64 \
  -t "$ADMIN_IMAGE_FULL" \
  -f "$PROJECT_ROOT/admin/Dockerfile" \
  "$PROJECT_ROOT/admin"

info "Pushing images to ACR …"
docker push "$IMAGE_FULL"
docker push "$ADMIN_IMAGE_FULL"
ok "Images pushed: $IMAGE_FULL, $ADMIN_IMAGE_FULL"

# ── 4. Update Container Apps to use the new image ───────────────────────────
# Setting DEPLOY_TIMESTAMP forces a new revision even when the image tag
# (e.g. "latest") hasn't changed, ensuring the container pulls the new image.
DEPLOY_TS="$(date -u +%Y%m%dT%H%M%SZ)"

info "Updating web container app …"
az containerapp update \
  --name "${APP_NAME}-web" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$IMAGE_FULL" \
  --set-env-vars "DEPLOY_TIMESTAMP=${DEPLOY_TS}" \
  --output none

info "Updating worker container app …"
az containerapp update \
  --name "${APP_NAME}-worker" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$IMAGE_FULL" \
  --set-env-vars "DEPLOY_TIMESTAMP=${DEPLOY_TS}" \
  --output none

info "Updating admin container app …"
az containerapp update \
  --name "${APP_NAME}-admin" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$ADMIN_IMAGE_FULL" \
  --set-env-vars "DEPLOY_TIMESTAMP=${DEPLOY_TS}" \
  --output none

ok "Container apps updated."

# ── 5. Show results ─────────────────────────────────────────────────────────
WEB_FQDN=$(az containerapp show \
  --name "${APP_NAME}-web" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

ADMIN_FQDN=$(az containerapp show \
  --name "${APP_NAME}-admin" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅  Deployment complete!"
echo ""
echo "  🌐  Web UI:  https://${WEB_FQDN}"
echo "  🛠️   Admin:  https://${ADMIN_FQDN}/admin/"
echo "  📦  Image:   ${IMAGE_FULL}"
echo "  🗄️   RG:      ${RESOURCE_GROUP}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
