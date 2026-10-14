// IB Gateway VM: Interactive Brokers' API gateway plus the WealthRyse live
// collector, which writes live portfolio data into the existing Postgres
// database. Staging, resource group rg-ibkr-database-v11, East Asia.
//
// - Not deployed by CI: CI only builds this file. Deploy it by hand (what-if
//   first) after the network change below is live.
// - The ibkr-gateway subnet (10.50.12.0/28 in ibkrv2stg-vnet, outbound through
//   the existing NAT gateway ibkrv2stg-admin-egress so its fixed IP stays) and
//   the private-endpoint NSG change that lets the subnet reach the vault and
//   registry private endpoints come from the network thread. This file takes
//   the subnet ID only. The network change also associates the NSG created here
//   with the subnet; this file attaches it to the NIC as well.
// - The VM has no public IP. Maintenance sign-in is Entra SSH
//   (AADSSHLoginForLinux) through the existing Bastion; password sign-in is off.
// - No secret passes through this template. At service start the VM reads
//   ibkr-username, ibkr-password and database-live-url from the main vault with
//   its user-assigned identity, into tmpfs only (deploy/ibkr-gateway/).
// - customData is fixed when the VM is created. To change an image digest or
//   the trading mode, edit /etc/ibkr-live/ibkr-live.env on the VM and restart
//   ibkr-live.service, or roll back and deploy again.
// - Rollback: delete the VM, its OS disk and its NIC (both are set to be
//   deleted with the VM). The identity, its role assignments, the NSG and the
//   patch schedule do nothing without the VM and can be deleted afterwards.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param vmName string = 'ibkrv2stg-ibkr-gateway'
param vmSize string = 'Standard_D2as_v5'
@description('Resource ID of the ibkr-gateway subnet (10.50.12.0/28 in ibkrv2stg-vnet), created by the network change.')
param subnetId string
param keyVaultName string = 'ibkrv2stg-auhecwc4itaoo'
param registryName string = 'ibkrv2stgauhecwc4itaoo'
@description('IB Gateway image copied from ghcr.io/gnzsnz/ib-gateway into the main registry, pinned by digest: <registry>.azurecr.io/ib-gateway@sha256:...')
param gatewayImage string
@description('Live collector image in the main registry, pinned by digest: <registry>.azurecr.io/ibkr-live-collector@sha256:...')
param collectorImage string
@allowed([
  'paper'
  'live'
])
@description('IB Gateway trading mode. Paper for the first runs; live later.')
param tradingMode string = 'paper'
@description('Comma-separated IBKR account IDs the collector reads.')
param liveAccountIds string = 'U16088571,U16088606'
param tenantId string = '00000000-0000-0000-0000-000000000001'
@allowed([
  1
  2
  3
  4
])
@description('IBKR market data type for the collector: 1 live, 2 frozen, 3 delayed, 4 delayed frozen.')
param liveMarketDataType int = 3
param bastionSubnetPrefix string = '10.50.8.0/26'
param postgresSubnetPrefix string = '10.50.4.0/28'
@description('Address range of the main vault and registry private endpoints.')
param privateEndpointPrefix string = '10.50.10.0/24'
@description('Outbound TCP ports IB Gateway uses to reach IBKR, in addition to 80 and 443.')
param ibkrPorts array = [
  '4000-4002'
]
param adminUsername string = 'platformadmin'
@description('OpenSSH public key whose private key is discarded, as for ibkrv2stg-private-admin: Azure needs one key when password sign-in is off, but sign-in is Entra-only through Bastion.')
param adminSshPublicKey string
@description('Entra object ID given Virtual Machine Administrator Login on this VM only. Empty skips the assignment.')
param adminGroupObjectId string = ''
@allowed([
  'Group'
  'User'
])
param adminPrincipalType string = 'Group'
@description('First day of the weekly patch window. Must be a Monday.')
param patchWindowStartDate string = '2026-10-19'

var tags = {
  application: 'ibkr-portfolio-platform'
  environment: 'staging'
  owner: 'portfolio-platform'
  costCenter: 'pilot'
  version: '2.0'
  dataClassification: 'confidential-financial'
}

var identityName = '${vmName}-identity'
var nsgName = '${vmName}-nsg'
var nicName = '${vmName}-nic'
var patchConfigurationName = '${vmName}-patch'
// The image's socat relay ports reachable from other containers (README
// "Ports"): 4003 forwards the live API port 4001, 4004 the paper port 4002.
var gatewayRelayPort = tradingMode == 'live' ? '4003' : '4004'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName

  resource ibkrUsername 'secrets' existing = {
    name: 'ibkr-username'
  }
  resource ibkrPassword 'secrets' existing = {
    name: 'ibkr-password'
  }
  resource databaseLiveUrl 'secrets' existing = {
    name: 'database-live-url'
  }
}

resource keyVaultSecretsUserRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '4633458b-17de-408a-b874-0445c86b69e6'
}
resource acrPullRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}
resource vmAdministratorLoginRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: '1c0163c0-47e6-4577-8991-ea5c82e286e4'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

// Key Vault Secrets User on the three secrets only, never vault-wide.
resource ibkrUsernameReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault::ibkrUsername.id, identity.id, keyVaultSecretsUserRole.id)
  scope: vault::ibkrUsername
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole.id
  }
}
resource ibkrPasswordReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault::ibkrPassword.id, identity.id, keyVaultSecretsUserRole.id)
  scope: vault::ibkrPassword
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole.id
  }
}
resource databaseLiveUrlReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault::databaseLiveUrl.id, identity.id, keyVaultSecretsUserRole.id)
  scope: vault::databaseLiveUrl
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRole.id
  }
}
resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole.id)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRole.id
  }
}

// Associated with the subnet by the network change; also attached to the NIC.
// Azure's DNS, host agent and instance metadata addresses (168.63.129.16 and
// 169.254.169.254) are not filtered by a plain deny rule; the explicit allows
// below only document what the VM uses there.
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: nsgName
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowBastionSshInbound'
        properties: {
          description: 'Maintenance sign-in (Entra SSH) from the existing Bastion only.'
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: bastionSubnetPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'DenyAllInbound'
        properties: {
          description: 'Nothing else comes in; the IB API port is never published.'
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'AllowPostgresOutbound'
        properties: {
          description: 'Live collector writes to the Postgres subnet.'
          priority: 100
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: postgresSubnetPrefix
          destinationPortRange: '5432'
        }
      }
      {
        name: 'AllowPrivateEndpointsOutbound'
        properties: {
          description: 'Main vault and registry private endpoints: secrets and images.'
          priority: 110
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: privateEndpointPrefix
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowAzureDnsOutbound'
        properties: {
          description: 'Azure-provided DNS.'
          priority: 120
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '168.63.129.16'
          destinationPortRange: '53'
        }
      }
      {
        name: 'AllowAzureHostAgentOutbound'
        properties: {
          description: 'Azure VM agent and health (WireServer).'
          priority: 130
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '168.63.129.16'
          destinationPortRanges: [
            '80'
            '32526'
          ]
        }
      }
      {
        name: 'AllowInstanceMetadataOutbound'
        properties: {
          description: 'Managed identity tokens (host only; containers are blocked on the VM).'
          priority: 140
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '169.254.169.254'
          destinationPortRange: '80'
        }
      }
      {
        // Room for later: the "broker container" from a separate plan (IBKR
        // Flex and Kiwoom REST calls from the same NAT IP) would use 443 here.
        // It is not part of this deployment.
        name: 'AllowInternetOutbound'
        properties: {
          description: 'Ubuntu updates, Entra sign-in, and IB Gateway to IBKR (80, 443 and the IBKR gateway ports).'
          priority: 200
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'Internet'
          destinationPortRanges: concat([
            '80'
            '443'
          ], ibkrPorts)
        }
      }
      {
        name: 'DenyAllOutbound'
        properties: {
          description: 'Nothing else goes out.'
          priority: 4096
          direction: 'Outbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: nicName
  location: location
  tags: tags
  properties: {
    enableIPForwarding: false
    networkSecurityGroup: {
      id: nsg.id
    }
    ipConfigurations: [
      {
        name: 'private'
        properties: {
          primary: true
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: subnetId
          }
        }
      }
    ]
  }
}

// Every value here is a name, ID, image reference, port or a file from
// deploy/ibkr-gateway/; none is a secret.
var cloudInitValues = {
  __KEY_VAULT_URI__: vault.properties.vaultUri
  __IDENTITY_CLIENT_ID__: identity.properties.clientId
  __REGISTRY_LOGIN_SERVER__: registry.properties.loginServer
  __GATEWAY_IMAGE__: gatewayImage
  __COLLECTOR_IMAGE__: collectorImage
  __TRADING_MODE__: tradingMode
  __IB_GATEWAY_PORT__: gatewayRelayPort
  __LIVE_ACCOUNT_IDS__: liveAccountIds
  __PORTFOLIO_TENANT_ID__: tenantId
  __LIVE_MARKET_DATA_TYPE__: string(liveMarketDataType)
  __DOCKER_COMPOSE_B64__: loadFileAsBase64('../deploy/ibkr-gateway/docker-compose.yml')
  __FETCH_SECRETS_B64__: loadFileAsBase64('../deploy/ibkr-gateway/fetch-secrets.sh')
  __RELOGIN_CHECK_B64__: loadFileAsBase64('../deploy/ibkr-gateway/relogin-check.sh')
  __IMDS_GUARD_SERVICE_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-imds-guard.service')
  __DOCKER_IMDS_GUARD_CONF_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/docker-imds-guard.conf')
  __IBKR_LIVE_SERVICE_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-live.service')
  __WEEKLY_RELOGIN_SERVICE_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-gateway-weekly-relogin.service')
  __WEEKLY_RELOGIN_TIMER_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-gateway-weekly-relogin.timer')
  __RELOGIN_CHECK_SERVICE_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-gateway-relogin-check.service')
  __RELOGIN_CHECK_TIMER_B64__: loadFileAsBase64('../deploy/ibkr-gateway/systemd/ibkr-gateway-relogin-check.timer')
}
var cloudInit = reduce(
  items(cloudInitValues),
  loadTextContent('../deploy/ibkr-gateway/cloud-init.yaml'),
  (text, entry) => replace(text, entry.key, entry.value)
)

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        name: '${vmName}-os'
        createOption: 'FromImage'
        diskSizeGB: 32
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
        deleteOption: 'Delete'
      }
    }
    osProfile: {
      computerName: 'ibkr-gateway'
      adminUsername: adminUsername
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminSshPublicKey
            }
          ]
        }
        patchSettings: {
          patchMode: 'AutomaticByPlatform'
          assessmentMode: 'AutomaticByPlatform'
          automaticByPlatformSettings: {
            bypassPlatformSafetyChecksOnUserSchedule: true
            rebootSetting: 'IfRequired'
          }
        }
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: {
            primary: true
            deleteOption: 'Delete'
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
  }
  // The first boot reads the secrets and pulls the images straight away.
  dependsOn: [
    ibkrUsernameReader
    ibkrPasswordReader
    databaseLiveUrlReader
    registryPull
  ]
}

resource entraSshLogin 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AADSSHLoginForLinux'
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADSSHLoginForLinux'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
  }
}

// Scoped to this VM only, never the resource group or subscription.
resource adminLogin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(adminGroupObjectId)) {
  name: guid(vm.id, adminGroupObjectId, vmAdministratorLoginRole.id)
  scope: vm
  properties: {
    principalId: adminGroupObjectId
    principalType: adminPrincipalType
    roleDefinitionId: vmAdministratorLoginRole.id
  }
  dependsOn: [
    entraSshLogin
  ]
}

// Azure Update Manager: Mondays 20:30 Hong Kong time ('China Standard Time' is
// Windows' name for UTC+8 Beijing, Chongqing, Hong Kong, Urumqi). The window
// has to be at least 1 hour 30 minutes long. Patching a small VM usually takes
// minutes, so a reboot normally lands before 21:00; its login is then the
// week's login and the 21:00 restart skips (relogin-check.sh). A reboot after
// 21:00 would mean one more IB Key push that evening.
resource patchWindow 'Microsoft.Maintenance/maintenanceConfigurations@2023-04-01' = {
  name: patchConfigurationName
  location: location
  tags: tags
  properties: {
    maintenanceScope: 'InGuestPatch'
    visibility: 'Custom'
    extensionProperties: {
      InGuestPatchMode: 'User'
    }
    maintenanceWindow: {
      startDateTime: '${patchWindowStartDate} 20:30'
      duration: '01:30'
      timeZone: 'China Standard Time'
      recurEvery: '1Week Monday'
    }
    installPatches: {
      rebootSetting: 'IfRequired'
      linuxParameters: {
        classificationsToInclude: [
          'Critical'
          'Security'
          'Other'
        ]
      }
    }
  }
}

resource patchAssignment 'Microsoft.Maintenance/configurationAssignments@2023-04-01' = {
  name: patchConfigurationName
  location: location
  scope: vm
  properties: {
    maintenanceConfigurationId: patchWindow.id
    resourceId: vm.id
  }
}

output vmName string = vm.name
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
