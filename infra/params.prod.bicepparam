using './main.bicep'

param environment = 'prod'

// Storage
param storageSku = 'Standard_GRS'
param containerName = 'm365-vaults'

// ACR
param acrSku = 'Standard'

// PostgreSQL (Burstable B2ms — more headroom for production)
param postgresSkuName = 'Standard_B2ms'
param postgresSkuTier = 'Burstable'
param postgresStorageGb = 64

// App Service (P1v2 — production tier with autoscale)
param appServicePlanSku = 'P1v2'

// postgresAdminPassword: passed via CLI --parameters override (secret)
