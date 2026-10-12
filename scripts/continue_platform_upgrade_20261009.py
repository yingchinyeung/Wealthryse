"""Fresh read-only preflight for remaining upgrade stages. No service mutations."""
import json,sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from deploy_metadata_monitor import request,BASE

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261009-stage5'
COLLECTIONS={
 'vnets':('Microsoft.Network/virtualNetworks','2024-05-01'),
 'private_endpoints':('Microsoft.Network/privateEndpoints','2024-05-01'),
 'private_dns':('Microsoft.Network/privateDnsZones','2024-06-01'),
 'databases':('Microsoft.DBforPostgreSQL/flexibleServers','2024-08-01'),
 'registries':('Microsoft.ContainerRegistry/registries','2023-07-01'),
 'vaults':('Microsoft.KeyVault/vaults','2023-07-01'),
 'storage':('Microsoft.Storage/storageAccounts','2023-05-01'),
 'environments':('Microsoft.App/managedEnvironments','2024-03-01'),
 'vms':('Microsoft.Compute/virtualMachines','2024-03-01'),
}
FIELDS={
 'vnets':['addressSpace','dhcpOptions','subnets','virtualNetworkPeerings','provisioningState'],
 'private_endpoints':['subnet','privateLinkServiceConnections','networkInterfaces','customDnsConfigs','provisioningState'],
 'private_dns':['numberOfRecordSets','numberOfVirtualNetworkLinks','provisioningState'],
 'databases':['state','version','network','storage','highAvailability','backup','availabilityZone','fullyQualifiedDomainName','replica'],
 'registries':['loginServer','publicNetworkAccess','networkRuleSet','privateEndpointConnections','dataEndpointEnabled','dataEndpointHostNames','provisioningState'],
 'vaults':['vaultUri','networkAcls','publicNetworkAccess','enableRbacAuthorization','privateEndpointConnections'],
 'storage':['primaryEndpoints','networkAcls','publicNetworkAccess','privateEndpointConnections','allowBlobPublicAccess','supportsHttpsTrafficOnly'],
 'environments':['vnetConfiguration','infrastructureResourceGroup','workloadProfiles','provisioningState'],
 'vms':['hardwareProfile','networkProfile','provisioningState'],
}
def collect(kind):
 path,api=COLLECTIONS[kind]
 response=request('GET',BASE+path+'?api-version='+api)
 items=list(response['value']);seen=set()
 while response.get('nextLink'):
  link=response['nextLink'];url=urlsplit(link)
  if url.scheme!='https' or url.netloc!='management.azure.com' or url.path.lower()!=(BASE+path).lower() or link in seen or len(seen)>=20:
   raise ValueError('Unexpected inventory pagination scope')
  seen.add(link);response=request('GET',url.path+'?'+url.query);items.extend(response['value'])
 rows=[]
 for item in items:
  row={k:item.get(k) for k in ('name','id','location','sku','identity')}
  row['properties']={k:item['properties'].get(k) for k in FIELDS[kind]}
  if kind=='private_dns':
   row['links']=request('GET',item['id']+'/virtualNetworkLinks?api-version='+api)['value']
  rows.append(row)
 return kind,rows
def main():
 OUT.mkdir(exist_ok=True)
 with ThreadPoolExecutor(max_workers=4) as pool:groups=dict(pool.map(collect,COLLECTIONS))
 record={'captured_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'resource_groups':groups}
 name='infrastructure-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json'
 with (OUT/name).open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
 print(json.dumps({'file':str(OUT/name),'counts':{k:len(v) for k,v in groups.items()}}))
if __name__=='__main__':main()
