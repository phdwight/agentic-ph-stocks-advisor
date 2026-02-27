// ---------------------------------------------------------------------------
// Bicep parameters file — override for your environment
// ---------------------------------------------------------------------------
using 'main.bicep'

param appName = 'phstocks'
param location = 'southeastasia'
param pgAdminUser = 'phadmin'
// Secrets — pass via CLI or environment variables:
//   az deployment group create ... \
//     --parameters main.bicepparam \
//     --parameters pgAdminPassword='<password>' \
//                  openaiApiKey='<key>' \
//                  tavilyApiKey='<key>'
param pgAdminPassword = ''
param openaiApiKey = ''
param tavilyApiKey = ''
param openaiModel = 'gpt-4o-mini'
param imageTag = 'latest'
