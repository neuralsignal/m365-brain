// m365-extract Azure infrastructure.
//
// Phase A: Storage + ACR + PostgreSQL + App Service + Container Instance + Key Vault
// Phase B (future): VNet + private endpoints + custom domain + Log Analytics

@description('Environment name used for resource naming (dev, test, prod)')
param environment string

@description('Azure region')
param location string = 'switzerlandnorth'

// --- Storage ---
@description('Storage account SKU')
param storageSku string = 'Standard_LRS'

@description('Blob container name')
param containerName string = 'm365-vaults'

// --- ACR ---
@description('Container Registry SKU')
param acrSku string = 'Basic'

// --- PostgreSQL ---
@description('PostgreSQL Flexible Server SKU name')
param postgresSkuName string = 'Standard_B1ms'

@description('PostgreSQL Flexible Server SKU tier')
param postgresSkuTier string = 'Burstable'

@description('PostgreSQL storage size in GB')
param postgresStorageGb int = 32

@description('PostgreSQL admin username')
param postgresAdminUser string = 'm365admin'

@secure()
@description('PostgreSQL admin password')
param postgresAdminPassword string

// --- App Service ---
@description('App Service Plan SKU')
param appServicePlanSku string = 'B1'

@description('Docker image tag for the web container')
param webImageTag string = 'latest'

@description('Docker image tag for the daemon container')
param daemonImageTag string = 'latest'

// --- App secrets (passed via CLI --parameters override) ---
@secure()
@description('Application secret key for session signing')
param secretKey string

@secure()
@description('Fernet key for token encryption')
param fernetKey string

@secure()
@description('Entra app client secret')
param entraClientSecret string

// --- App config (non-secret) ---
@description('Entra app client ID')
param entraClientId string

@description('Entra tenant ID')
param entraTenantId string

@description('Admin email address for the UI')
param adminEmail string

// --- Naming ---
var storageAccountName = 'stm365ext${environment}'
var acrName = 'acrm365ext${environment}'
var postgresServerName = 'psql-m365-extract-${environment}'
var postgresDbName = 'm365extract'
var appServicePlanName = 'asp-m365-extract-${environment}'
var webAppName = 'app-m365-admin-${environment}'
var daemonContainerGroupName = 'ci-m365-daemon-${environment}'
var keyVaultName = 'kv-m365-ext-${environment}'

// Derived values
var databaseUrl = 'postgresql://${postgresAdminUser}:${postgresAdminPassword}@${postgresServer.properties.fullyQualifiedDomainName}:5432/${postgresDbName}?sslmode=require'

// ============================================================================
// Storage Account + Blob Container
// ============================================================================

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: storageSku
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    accessTier: 'Hot'
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: containerName
}

// ============================================================================
// Azure Container Registry
// ============================================================================

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: acrSku
  }
  properties: {
    adminUserEnabled: true
  }
}

// ============================================================================
// PostgreSQL Flexible Server
// ============================================================================

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: postgresServerName
  location: location
  sku: {
    name: postgresSkuName
    tier: postgresSkuTier
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdminUser
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: postgresStorageGb
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    // Allow Azure services (App Service, Container Instances) to connect
    network: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// Firewall rule: allow Azure services
resource postgresFirewallAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgresServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource postgresDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgresServer
  name: postgresDbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// ============================================================================
// Key Vault
// ============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// NOTE: Key Vault RBAC role assignment for App Service managed identity
// requires User Access Administrator or Owner role on the subscription.
// Current deployer role is Contributor — this must be done manually:
//
//   az role assignment create \
//     --assignee <webApp-principalId> \
//     --role "Key Vault Secrets User" \
//     --scope /subscriptions/<sub-id>/resourceGroups/rg-m365-extract-<env>/providers/Microsoft.KeyVault/vaults/kv-m365-ext-<env>

// ============================================================================
// App Service Plan (Linux containers)
// ============================================================================

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: {
    name: appServicePlanSku
  }
  properties: {
    reserved: true // Required for Linux
  }
}

// ============================================================================
// App Service — Reflex Admin UI
// ============================================================================

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  kind: 'app,linux,container'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acr.properties.loginServer}/m365-admin:${webImageTag}'
      alwaysOn: true
      appSettings: [
        {
          name: 'DOCKER_REGISTRY_SERVER_URL'
          value: 'https://${acr.properties.loginServer}'
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_USERNAME'
          value: acr.listCredentials().username
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_PASSWORD'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE'
          value: 'false'
        }
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          name: 'DATABASE_URL'
          value: databaseUrl
        }
        {
          name: 'SECRET_KEY'
          value: secretKey
        }
        {
          name: 'FERNET_KEY'
          value: fernetKey
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: entraClientId
        }
        {
          name: 'AZURE_TENANT_ID'
          value: entraTenantId
        }
        {
          name: 'AZURE_CLIENT_SECRET'
          value: entraClientSecret
        }
        {
          name: 'M365_ADMIN_REDIRECT_URI'
          value: 'https://${webAppName}.azurewebsites.net/callback'
        }
        {
          name: 'M365_ADMIN_CONFIG'
          value: './config.deploy.yaml'
        }
        {
          name: 'ADMIN_EMAIL'
          value: adminEmail
        }
      ]
    }
    httpsOnly: true
  }
}

// ============================================================================
// Container Instance — Sync Daemon
// ============================================================================

resource daemonContainer 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: daemonContainerGroupName
  location: location
  properties: {
    osType: 'Linux'
    restartPolicy: 'Always'
    containers: [
      {
        name: 'm365-daemon'
        properties: {
          image: '${acr.properties.loginServer}/m365-daemon:${daemonImageTag}'
          resources: {
            requests: {
              cpu: 1
              memoryInGB: 1
            }
          }
          environmentVariables: [
            {
              name: 'DATABASE_URL'
              secureValue: databaseUrl
            }
            {
              name: 'SECRET_KEY'
              secureValue: secretKey
            }
            {
              name: 'FERNET_KEY'
              secureValue: fernetKey
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'AZURE_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'AZURE_CLIENT_SECRET'
              secureValue: entraClientSecret
            }
            {
              name: 'AZURE_STORAGE_CONNECTION_STRING'
              secureValue: storageAccount.listKeys().keys[0].value != '' ? 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=core.windows.net' : ''
            }
            {
              name: 'AZURE_STORAGE_CONTAINER'
              value: containerName
            }
            {
              name: 'AZURE_STORAGE_PREFIX'
              value: '${environment}/'
            }
            {
              name: 'M365_ADMIN_CONFIG'
              value: './config.deploy.yaml'
            }
            {
              name: 'ADMIN_EMAIL'
              value: adminEmail
            }
          ]
        }
      }
    ]
    imageRegistryCredentials: [
      {
        server: acr.properties.loginServer
        username: acr.listCredentials().username
        password: acr.listCredentials().passwords[0].value
      }
    ]
  }
}

// ============================================================================
// Outputs
// ============================================================================

output storageAccountName string = storageAccount.name
output containerName string = container.name
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output postgresHost string = postgresServer.properties.fullyQualifiedDomainName
output postgresDbName string = postgresDbName
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output webAppName string = webApp.name
output webAppPrincipalId string = webApp.identity.principalId
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
