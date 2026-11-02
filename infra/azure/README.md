# azure

## Overview
Azure Bicep infrastructure-as-code templates (`main.bicep`, `main.bicepparam`) and operational shell scripts (`deploy.sh`, `teardown.sh`) for provisioning, updating, and tearing down the cloud deployment of PH Stocks Advisor on Azure Container Apps, Azure Database for PostgreSQL Flexible Server, Azure Container Registry, and Log Analytics.

## Interface
- **Bicep Parameters (`[main.bicep]`, `[main.bicepparam]`)**:
  - `appName` (`[main.bicep:appName]`): Application prefix string (default `'phstocks'`).
  - `location` (`[main.bicep:location]`): Azure region string (default `resourceGroup().location`; `'southeastasia'` in `[main.bicepparam]`).
  - `pgAdminUser` (`[main.bicep:pgAdminUser]`): PostgreSQL administrator username (default `'phadmin'`).
  - `pgAdminPassword` (`[main.bicep:pgAdminPassword]`): Secure PostgreSQL administrator password (required).
  - `openaiApiKey` (`[main.bicep:openaiApiKey]`): Secure OpenAI API key (required).
  - `tavilyApiKey` (`[main.bicep:tavilyApiKey]`): Secure Tavily API key (default `''`).
  - `openaiModel` (`[main.bicep:openaiModel]`): OpenAI model name (default `'gpt-4o-mini'`).
  - `langfusePublicKey`, `langfuseSecretKey`, `langfuseHost`, `langfuseEnvironment` (`[main.bicep:langfusePublicKey]`, `[main.bicep:langfuseSecretKey]`, `[main.bicep:langfuseHost]`, `[main.bicep:langfuseEnvironment]`): Langfuse observability parameters.
  - `entraClientId`, `entraClientSecret`, `entraTenantId` (`[main.bicep:entraClientId]`, `[main.bicep:entraClientSecret]`, `[main.bicep:entraTenantId]`): Microsoft Entra ID OAuth parameters.
  - `googleClientId`, `googleClientSecret` (`[main.bicep:googleClientId]`, `[main.bicep:googleClientSecret]`): Google OAuth parameters.
  - `flaskSecretKey` (`[main.bicep:flaskSecretKey]`): Secure Flask session key.
  - `adminSecretKey` (`[main.bicep:adminSecretKey]`): Secure SQLAdmin secret key.
  - `pgSkuName`, `pgSkuTier` (`[main.bicep:pgSkuName]`, `[main.bicep:pgSkuTier]`): PostgreSQL SKU settings (defaults `'Standard_B1ms'`, `'Burstable'`).
  - `imageTag` (`[main.bicep:imageTag]`): Container image tag (default `'latest'`).
  - `webCpu`, `webMemory`, `workerCpu`, `workerMemory`, `redisCpu`, `redisMemory`, `redisMaxMemory` (`[main.bicep:webCpu]`, `[main.bicep:webMemory]`, `[main.bicep:workerCpu]`, `[main.bicep:workerMemory]`, `[main.bicep:redisCpu]`, `[main.bicep:redisMemory]`, `[main.bicep:redisMaxMemory]`): Container resource limits.
  - `webWorkers`, `pgPoolMax`, `redisMaxConnections`, `webMaxReplicas`, `workerMaxReplicas`, `celeryConcurrency` (`[main.bicep:webWorkers]`, `[main.bicep:pgPoolMax]`, `[main.bicep:redisMaxConnections]`, `[main.bicep:webMaxReplicas]`, `[main.bicep:workerMaxReplicas]`, `[main.bicep:celeryConcurrency]`): Concurrency, connection pool, and replica scaling parameters.
- **Bicep Outputs (`[main.bicep]`)**:
  - `acrLoginServer` (`[main.bicep:acrLoginServer]`): ACR login server hostname.
  - `acrName` (`[main.bicep:acrName]`): ACR resource name.
  - `webAppUrl` (`[main.bicep:webAppUrl]`): External HTTPS URL for the web Container App.
  - `adminAppUrl` (`[main.bicep:adminAppUrl]`): Internal HTTPS URL for the admin Container App.
  - `pgServerFqdn` (`[main.bicep:pgServerFqdn]`): PostgreSQL Flexible Server FQDN.
  - `redisInternalFqdn` (`[main.bicep:redisInternalFqdn]`): Internal Redis Container App FQDN.
- **Deployment CLI (`[deploy.sh]`)**:
  - Flags: `--update` (`[deploy.sh:--update]`, rebuild/push images and update Container Apps only) and `--infra-only` (`[deploy.sh:--infra-only]`, provision Bicep infrastructure only).
  - Environment variables: `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_APP_NAME`, `AZURE_PG_ADMIN_USER`, `IMAGE_TAG`, `OPENAI_API_KEY`, `AZURE_PG_PASSWORD`.
  - Shell functions: `info`, `ok`, `warn` (`[deploy.sh:info]`, `[deploy.sh:ok]`, `[deploy.sh:warn]`).
- **Teardown CLI (`[teardown.sh]`)**:
  - Flag: `--yes` (`[teardown.sh:--yes]`, skip confirmation prompt).
  - Environment variable: `AZURE_RESOURCE_GROUP` (`[teardown.sh:AZURE_RESOURCE_GROUP]`, default `'ph-stocks-advisor-rg'`).

## Invariants
- **Resource Group Scope & Constraints** (`[main.bicep:targetScope]`, `[main.bicep:appName]`, `[main.bicep:pgSkuTier]`): Bicep `targetScope` is `'resourceGroup'`; `appName` length must be between 3 and 16 characters; `pgSkuTier` must be `'Burstable'` or `'GeneralPurpose'`.
- **Optional Secret Sentinel** (`[main.bicep:secrets]`): Optional secret parameters (`tavilyApiKey`, `langfusePublicKey`, `langfuseSecretKey`, `entraClientId`, `entraClientSecret`, `googleClientId`, `googleClientSecret`) fall back to the literal string `'NOTSET'` when empty so Container App secret definitions remain valid.
- **Internal Redis URL** (`[main.bicep:redisInternalUrl]`): Hardcoded to `'redis://phstocks-redis:6379/0'` with internal TCP ingress on port `6379` (`minReplicas: 1`, `maxReplicas: 1`).
- **Ingress & Health Probe Boundaries** (`[main.bicep:webApp]`, `[main.bicep:adminApp]`): `webApp` exposes external HTTPS ingress on `targetPort: 5000` with liveness and readiness probes targeting `GET /healthz`; `adminApp` uses internal-only ingress (`external: false`) on `targetPort: 8085`.
- **Fail-Fast Script Execution** (`[deploy.sh:set -euo pipefail]`, `[teardown.sh:set -euo pipefail]`): Both shell scripts run under `set -euo pipefail`. `deploy.sh` exits immediately if `OPENAI_API_KEY` is missing, if `AZURE_PG_PASSWORD` is missing during full deployments, or if an unknown flag is passed. `teardown.sh` requires interactive `'yes'` confirmation unless `--yes` is provided.

## Side Effects
- **Azure Resource Provisioning** (`[main.bicep]`, `[deploy.sh]`): Creates/updates an Azure Resource Group (`az group create`) and deploys Azure Container Registry (`acr`), Log Analytics Workspace (`logAnalytics`), PostgreSQL Flexible Server v16 (`pgServer`, firewall rule `AllowAzureServices`, database `ph_advisor`), Container Apps Managed Environment (`containerAppEnv`), and four Container Apps (`${appName}-redis`, `${appName}-web`, `${appName}-worker`, `${appName}-admin`).
- **Local Environment & Docker Operations** (`[deploy.sh]`): Sources `$PROJECT_ROOT/.env` if present, authenticates local Docker against ACR (`az acr login`), builds and pushes `linux/amd64` container images, and rolls out updated images with `DEPLOY_TIMESTAMP` via `az containerapp update`.
- **Resource Group Deletion** (`[teardown.sh:az group delete]`): Initiates asynchronous deletion (`--yes --no-wait`) of the target Azure Resource Group and all contained resources.

## Verification
- Run: `pytest`
