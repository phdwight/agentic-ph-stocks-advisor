// ---------------------------------------------------------------------------
// PH Stocks Advisor — Azure Infrastructure (Bicep)
//
// Deploys:
//   1. Azure Container Registry (ACR)
//   2. Azure Database for PostgreSQL — Flexible Server
//   3. Log Analytics Workspace
//   4. Azure Container Apps Environment
//   5. Container App: redis (Redis 7 Alpine — fast, no managed-service wait)
//   6. Container App: web (Flask)
//   7. Container App: worker (Celery)
//
// NOTE: Redis runs as a Container App instead of Azure Cache for Redis
// because the managed service takes 20-40 min to provision.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// ── Parameters ──────────────────────────────────────────────────────────────

@description('Base name prefix for all resources (lowercase, no special chars).')
@minLength(3)
@maxLength(16)
param appName string = 'phstocks'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('PostgreSQL administrator login name.')
param pgAdminUser string = 'phadmin'

@secure()
@description('PostgreSQL administrator password.')
param pgAdminPassword string

@secure()
@description('OpenAI API key.')
param openaiApiKey string

@secure()
@description('Tavily API key (optional — leave empty to skip web search).')
param tavilyApiKey string = ''

@description('OpenAI model name.')
param openaiModel string = 'gpt-4o-mini'

@secure()
@description('LangSmith API key (optional — for tracing).')
param langsmithApiKey string = ''

@description('LangSmith project name.')
param langsmithProject string = 'ph-stocks-advisor'

@secure()
@description('Microsoft Entra ID application (client) ID (optional — leave empty to disable auth).')
param entraClientId string = ''

@secure()
@description('Microsoft Entra ID client secret.')
param entraClientSecret string = ''

@description('Microsoft Entra ID tenant ID (or "common" for multi-tenant).')
param entraTenantId string = 'common'

@secure()
@description('Flask session encryption secret key.')
param flaskSecretKey string = 'ph-stocks-advisor-change-me-in-production'

@secure()
@description('Google OAuth2 client ID (optional — leave empty to disable Google login).')
param googleClientId string = ''

@secure()
@description('Google OAuth2 client secret.')
param googleClientSecret string = ''

@secure()
@description('Secret key for the SQLAdmin session middleware.')
param adminSecretKey string = 'sqladmin-change-me-in-production'

@description('PostgreSQL SKU name. Use Standard_B1ms (Burstable) for low traffic, Standard_D2ds_v5 (GeneralPurpose) for high traffic.')
param pgSkuName string = 'Standard_B1ms'

@description('PostgreSQL SKU tier. Must match pgSkuName: Burstable for B-series, GeneralPurpose for D-series.')
@allowed(['Burstable', 'GeneralPurpose'])
param pgSkuTier string = 'Burstable'

@description('Docker image tag to deploy.')
param imageTag string = 'latest'

// ── Scaling parameters ──────────────────────────────────────────────────────
// Defaults are sized for hobby / low-traffic (~100 users/month).
// Override via deploy parameters for higher tiers — see README.

@description('Web container CPU cores. 0.25 hobby, 0.5 small, 2 high traffic.')
param webCpu string = '0.25'

@description('Web container memory. 0.5Gi hobby, 1Gi small, 4Gi high traffic.')
param webMemory string = '0.5Gi'

@description('Worker container CPU cores. 0.25 hobby, 0.5 small, 1 high traffic.')
param workerCpu string = '0.25'

@description('Worker container memory. 0.5Gi hobby, 1Gi small, 2Gi high traffic.')
param workerMemory string = '0.5Gi'

@description('Redis container CPU cores. 0.25 hobby/small, 0.5 high traffic.')
param redisCpu string = '0.25'

@description('Redis container memory. 0.5Gi hobby/small, 1Gi high traffic.')
param redisMemory string = '0.5Gi'

@description('Redis maxmemory limit. 64mb hobby, 128mb small, 512mb high traffic.')
param redisMaxMemory string = '64mb'

@description('Gunicorn worker processes. 1 hobby, 2 small, 4 high traffic.')
param webWorkers string = '1'

@description('Max PostgreSQL connections in the pool. 5 hobby, 10 small, 20 high traffic.')
param pgPoolMax string = '5'

@description('Max Redis connections in the shared pool. 10 hobby, 20 small, 50 high traffic.')
param redisMaxConnections string = '10'

@description('Max web replicas for autoscaling. 1 hobby, 3 small, 10 high traffic.')
param webMaxReplicas int = 1

@description('Max worker replicas for autoscaling. 1 hobby, 3 small, 10 high traffic.')
param workerMaxReplicas int = 1

@description('Celery worker concurrency (prefork processes). 2 hobby, 4 small/high traffic.')
param celeryConcurrency string = '2'

// ── Derived names ───────────────────────────────────────────────────────────

var uniqueSuffix = uniqueString(resourceGroup().id)
var acrName = '${appName}acr${uniqueSuffix}'
var pgServerName = '${appName}-pg-${uniqueSuffix}'
var logAnalyticsName = '${appName}-logs-${uniqueSuffix}'
var envName = '${appName}-env'
var pgDatabaseName = 'ph_advisor'

// ── Container Registry ──────────────────────────────────────────────────────

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ── Log Analytics ───────────────────────────────────────────────────────────

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Azure Database for PostgreSQL — Flexible Server ─────────────────────────

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: pgServerName
  location: location
  sku: {
    name: pgSkuName
    tier: pgSkuTier
  }
  properties: {
    version: '16'
    administratorLogin: pgAdminUser
    administratorLoginPassword: pgAdminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

// Allow Azure services to connect to the database
resource pgFirewallRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: pgServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Create the application database
resource pgDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: pgServer
  name: pgDatabaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// ── Container Apps Environment ──────────────────────────────────────────────

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Container App: Redis ────────────────────────────────────────────────────
// Runs Redis 7 Alpine as an internal-only container app (deploys in seconds).

resource redisApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appName}-redis'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 6379
        exposedPort: 6379
        transport: 'tcp'
      }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: 'docker.io/redis:7-alpine'
          command: ['redis-server']
          args: ['--maxmemory', redisMaxMemory, '--maxmemory-policy', 'allkeys-lru']
          resources: {
            cpu: json(redisCpu)
            memory: redisMemory
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ── Shared environment variables (mirroring docker-compose) ─────────────────

// Redis URL uses the short app name (not FQDN) for internal TCP connectivity
var redisInternalUrl = 'redis://phstocks-redis:6379/0'

var sharedEnv = [
  { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
  { name: 'OPENAI_MODEL', value: openaiModel }
  { name: 'OPENAI_TEMPERATURE', value: '0.0' }
  { name: 'TAVILY_API_KEY', secretRef: 'tavily-api-key' }
  { name: 'TAVILY_MAX_RESULTS', value: '5' }
  { name: 'TAVILY_SEARCH_DEPTH', value: 'basic' }
  { name: 'DB_BACKEND', value: 'postgres' }
  { name: 'POSTGRES_DSN', secretRef: 'postgres-dsn' }
  { name: 'REDIS_URL', secretRef: 'redis-url' }
  { name: 'DRAGONFI_BASE_URL', value: 'https://api.dragonfi.ph/api/v2' }
  { name: 'PSE_EDGE_BASE_URL', value: 'https://edge.pse.com.ph' }
  { name: 'TRADINGVIEW_SCANNER_URL', value: 'https://scanner.tradingview.com/philippines/scan' }
  { name: 'HTTP_TIMEOUT', value: '15' }
  { name: 'TIMEZONE', value: 'Asia/Manila' }
  { name: 'OUTPUT_DIR', value: '/app/output' }
  { name: 'LANGSMITH_API_KEY', secretRef: 'langsmith-api-key' }
  { name: 'LANGSMITH_TRACING', value: 'true' }
  { name: 'LANGSMITH_PROJECT', value: langsmithProject }
  { name: 'ENTRA_CLIENT_ID', secretRef: 'entra-client-id' }
  { name: 'ENTRA_CLIENT_SECRET', secretRef: 'entra-client-secret' }
  { name: 'ENTRA_TENANT_ID', value: entraTenantId }
  { name: 'ENTRA_REDIRECT_PATH', value: '/auth/callback' }
  { name: 'FLASK_SECRET_KEY', secretRef: 'flask-secret-key' }
  { name: 'GOOGLE_CLIENT_ID', secretRef: 'google-client-id' }
  { name: 'GOOGLE_CLIENT_SECRET', secretRef: 'google-client-secret' }
  { name: 'GOOGLE_REDIRECT_PATH', value: '/auth/google/callback' }
  // Gunicorn tuning — custom gevent worker skips SSL monkey-patching
  // to avoid infinite recursion on Python 3.12 + OpenSSL ≥ 3.5
  { name: 'WEB_WORKERS', value: webWorkers }
  { name: 'WEB_WORKER_CLASS', value: 'ph_stocks_advisor.web.worker.GeventWorkerNoSSL' }
  { name: 'WEB_WORKER_CONNECTIONS', value: '1000' }
  { name: 'WEB_TIMEOUT', value: '120' }
  // Connection pools
  { name: 'PG_POOL_MIN', value: '2' }
  { name: 'PG_POOL_MAX', value: pgPoolMax }
  { name: 'REDIS_MAX_CONNECTIONS', value: redisMaxConnections }
]

var secrets = [
  { name: 'openai-api-key', value: openaiApiKey }
  { name: 'tavily-api-key', value: empty(tavilyApiKey) ? 'NOTSET' : tavilyApiKey }
  { name: 'postgres-dsn', value: 'postgresql://${pgAdminUser}:${pgAdminPassword}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDatabaseName}?sslmode=require' }
  { name: 'redis-url', value: redisInternalUrl }
  { name: 'langsmith-api-key', value: empty(langsmithApiKey) ? 'NOTSET' : langsmithApiKey }
  { name: 'entra-client-id', value: empty(entraClientId) ? 'NOTSET' : entraClientId }
  { name: 'entra-client-secret', value: empty(entraClientSecret) ? 'NOTSET' : entraClientSecret }
  { name: 'flask-secret-key', value: flaskSecretKey }
  { name: 'google-client-id', value: empty(googleClientId) ? 'NOTSET' : googleClientId }
  { name: 'google-client-secret', value: empty(googleClientSecret) ? 'NOTSET' : googleClientSecret }
  { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
]

var registries = [
  {
    server: acr.properties.loginServer
    username: acr.listCredentials().username
    passwordSecretRef: 'acr-password'
  }
]

var imageName = '${acr.properties.loginServer}/ph-stocks-advisor:${imageTag}'
var adminImageName = '${acr.properties.loginServer}/ph-stocks-advisor-admin:${imageTag}'

// ── Container App: web ──────────────────────────────────────────────────────

resource webApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appName}-web'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 5000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'web'
          image: imageName
          command: ['ph-advisor-web']
          args: ['--host', '0.0.0.0', '--port', '5000']
          // Gunicorn with gevent workers handles SSE streams
          // efficiently — each stream is a lightweight greenlet.
          resources: {
            cpu: json(webCpu)
            memory: webMemory
          }
          env: sharedEnv
          probes: [
            {
              type: 'liveness'
              httpGet: {
                path: '/healthz'
                port: 5000
              }
              initialDelaySeconds: 10
              periodSeconds: 15
              failureThreshold: 3
              timeoutSeconds: 5
            }
            {
              type: 'readiness'
              httpGet: {
                path: '/healthz'
                port: 5000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 3
              timeoutSeconds: 5
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: webMaxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

// ── Container App: worker ───────────────────────────────────────────────────

resource workerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appName}-worker'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      registries: registries
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: imageName
          command: ['celery']
          args: ['-A', 'ph_stocks_advisor.web.celery_app:celery_app', 'worker', '--loglevel=info', '--concurrency=${celeryConcurrency}', '--pool=prefork']
          resources: {
            cpu: json(workerCpu)
            memory: workerMemory
          }
          env: sharedEnv
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: workerMaxReplicas
      }
    }
  }
}

// ── Container App: admin (SQLAdmin panel) ───────────────────────────────────

resource adminApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appName}-admin'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8085
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
      secrets: [
        { name: 'postgres-dsn', value: 'postgresql://${pgAdminUser}:${pgAdminPassword}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDatabaseName}?sslmode=require' }
        { name: 'admin-secret-key', value: adminSecretKey }
        { name: 'acr-password', value: acr.listCredentials().passwords[0].value }
      ]
    }
    template: {
      containers: [
        {
          name: 'admin'
          image: adminImageName
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'DATABASE_URL', secretRef: 'postgres-dsn' }
            { name: 'ADMIN_SECRET_KEY', secretRef: 'admin-secret-key' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output webAppUrl string = 'https://${webApp.properties.configuration.ingress.fqdn}'
output adminAppUrl string = 'https://${adminApp.properties.configuration.ingress.fqdn}'
output pgServerFqdn string = pgServer.properties.fullyQualifiedDomainName
output redisInternalFqdn string = redisApp.properties.configuration.ingress.fqdn
