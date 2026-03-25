using './main.bicep'

param environment = 'dev'

// Storage
param storageSku = 'Standard_LRS'
param containerName = 'm365-vaults-dev'

// ACR
param acrSku = 'Basic'

// PostgreSQL (Burstable B1ms — cheapest option)
param postgresSkuName = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageGb = 32

// App Service (B1 — basic tier)
param appServicePlanSku = 'B1'

// postgresAdminPassword: passed via CLI --parameters override (secret)
