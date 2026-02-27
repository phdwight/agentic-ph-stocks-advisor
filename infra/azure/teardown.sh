#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# teardown.sh — Remove all Azure resources for PH Stocks Advisor
#
# Usage:
#   ./infra/azure/teardown.sh              # interactive confirmation
#   ./infra/azure/teardown.sh --yes        # skip confirmation
# ---------------------------------------------------------------------------

set -euo pipefail

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-ph-stocks-advisor-rg}"

if [[ "${1:-}" != "--yes" ]]; then
  echo "⚠️  This will DELETE the entire resource group: $RESOURCE_GROUP"
  echo "   All resources (database, cache, container apps) will be destroyed."
  read -rp "Type 'yes' to confirm: " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "🗑  Deleting resource group '$RESOURCE_GROUP' …"
az group delete --name "$RESOURCE_GROUP" --yes --no-wait
echo "✅  Deletion initiated (runs in background)."
echo "   Check status: az group show --name $RESOURCE_GROUP"
