targetScope = 'resourceGroup'

param appName string = 'ibkrv2stg-web'
param clientId string
// This is the name of a Container App secret setting, never its secret value.
param authCredentialSettingName string = 'microsoft-provider-authentication-secret'
param openIdIssuer string
param sessionLifetime string = '08:00:00'

resource web 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: appName
}

resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: web
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      redirectToProvider: 'AzureActiveDirectory'
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        registration: {
          clientId: clientId
          clientSecretSettingName: authCredentialSettingName
          openIdIssuer: openIdIssuer
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: false
      cookieExpiration: {
        convention: 'FixedTime'
        timeToExpiration: sessionLifetime
      }
      nonce: {
        validateNonce: true
        nonceExpirationInterval: '00:05:00'
      }
      tokenStore: {
        enabled: false
      }
    }
  }
}

output authenticationConfigName string = auth.name
output fixedSessionLifetime string = sessionLifetime
