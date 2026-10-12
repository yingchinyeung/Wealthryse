"""Read-only control-plane projections; excludes credentials and secret values."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from audit_azure_runtime import az,RG
OUT=Path(__file__).resolve().parents[1]/'.work/platform-review-20261009-v1'
queries={
 'web_auth':('containerapp','auth','show','-g',RG,'-n','ibkrv2stg-web','--query','{platform:platform,globalValidation:globalValidation,httpSettings:httpSettings,login:login,providerEnabled:identityProviders.azureActiveDirectory.enabled,registration:identityProviders.azureActiveDirectory.registration.clientId}'),
 'web_runtime':('containerapp','show','-g',RG,'-n','ibkrv2stg-web','--query','{ingress:properties.configuration.ingress,probes:properties.template.containers[].probes,scale:properties.template.scale,env:properties.template.containers[].env[?name==`PORTFOLIO_AUTH_MODE` || name==`PORTFOLIO_AI_ANALYST_ENABLED` || name==`REPORT_DESIGN_UPDATES_ENABLED`]}'),
 'vm':('vm','list','-g',RG,'--show-details','--query','[].{name:name,powerState:powerState,publicIps:publicIps,size:hardwareProfile.vmSize,os:storageProfile.osDisk.osType}'),
 'bastion':('network','bastion','list','-g',RG,'--query','[].{name:name,state:provisioningState,sku:sku.name,tunneling:enableTunneling}'),
 'peering':('network','vnet','list','-g',RG,'--query','[].{name:name,peerings:virtualNetworkPeerings[].{name:name,state:peeringState,access:allowVirtualNetworkAccess,forward:allowForwardedTraffic}}'),
 'acr':('acr','list','-g',RG,'--query','[].{name:name,sku:sku.name,public:publicNetworkAccess,anonymous:anonymousPullEnabled,admin:adminUserEnabled,network:networkRuleSet}'),
 'actions':('monitor','action-group','list','-g',RG,'--query','[].{name:name,email:length(emailReceivers),sms:length(smsReceivers),webhooks:length(webhookReceivers),azureAppPush:length(azureAppPushReceivers),armRoles:armRoleReceivers}'),
}
for vault in ('ibkrv11-5rcc62xxqblay','ibkrv2stg-auhecwc4itaoo','ibkrv2flexauhecwc4ita'):
    queries['vault_'+vault]=('keyvault','show','-n',vault,'--query','{name:name,public:properties.publicNetworkAccess,network:properties.networkAcls,rbac:properties.enableRbacAuthorization,softDelete:properties.enableSoftDelete,purge:properties.enablePurgeProtection}')
inventory=json.loads((OUT/'resource-group-inventory.json').read_text())
types={'Microsoft.Storage/storageAccounts','Microsoft.KeyVault/vaults','Microsoft.ContainerRegistry/registries','Microsoft.DBforPostgreSQL/flexibleServers','Microsoft.CognitiveServices/accounts'}
for row in inventory['resources']:
    if row['type'] in types:queries['diagnostics_'+row['type'].split('/')[-1]+'_'+row['name']]=('monitor','diagnostic-settings','list','--resource',row['id'])
def run(item):
    name,args=item
    try:return name,az(*args)
    except Exception as e:return name,{'error_type':type(e).__name__}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:result=dict(pool.map(run,queries.items()))
    result['captured_at']=datetime.now(timezone.utc).isoformat()
    (OUT/'control-details.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
