@description('Environment name used for resource naming (dev, test, prod)')
param environment string

@description('Azure region')
param location string = 'switzerlandnorth'

@description('Storage account SKU')
param storageSku string = 'Standard_LRS'

@description('Blob container name')
param containerName string = 'm365-vaults'

var storageAccountName = 'stm365ext${environment}'

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

output storageAccountName string = storageAccount.name
output containerName string = container.name
