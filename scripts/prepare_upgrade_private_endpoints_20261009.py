"""Freeze additive private endpoints only. No DNS links, peering or public cuts."""
import json,ipaddress
from pathlib import Path
from deploy_metadata_monitor import request,BASE
from run_specialist_gateway_read_verification import retain
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage6'
OUT=ROOT/'additive-endpoints-v2'
NETWORK_API='2024-05-01'
TARGETS=(
 ('main','Microsoft.ContainerRegistry/registries','ibkrv2stgauhecwc4itaoo','registry','2023-07-01'),
 ('main','Microsoft.KeyVault/vaults','ibkrv2stg-auhecwc4itaoo','vault','2023-07-01'),
 ('main','Microsoft.KeyVault/vaults','ibkrv2flexauhecwc4ita','vault','2023-07-01'),
 ('main','Microsoft.Storage/storageAccounts','ibkrv2stgauhecwc4itaoo','blob','2023-05-01'),
 ('legacy','Microsoft.ContainerRegistry/registries','ibkrv115rcc62xxqblay','registry','2023-07-01'),
 ('legacy','Microsoft.KeyVault/vaults','ibkrv11-5rcc62xxqblay','vault','2023-07-01'),
 ('legacy','Microsoft.Storage/storageAccounts','ibkrv115rcc62xxqblay','blob','2023-05-01'))
NETWORKS={'main':('ibkrv2stg-vnet','10.50.10.0/27',['10.50.0.0/23','10.50.9.0/27']),
          'legacy':('ibkrv11-vnet','10.40.10.0/27',['10.40.0.0/23','10.40.16.0/24'])}
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)
def validate_target_baselines(targets):
    expected={BASE+kind+'/'+name for _,kind,name,_,_ in TARGETS}
    if set(targets)!=expected or len(targets)!=7:raise ValueError('Each full target resource needs its own baseline')
    if any(value['id'].lower()!=key.lower() for key,value in targets.items()):raise ValueError('Baseline identity collision')
def main():
    if OUT.exists():raise ValueError('Preserve existing network plan')
    resources=[];baselines={};targets={};expected=[]
    for key,(vnet,cidr,callers) in NETWORKS.items():
        current=request('GET',BASE+'Microsoft.Network/virtualNetworks/'+vnet+'?api-version='+NETWORK_API)
        props=current['properties'];candidate=ipaddress.ip_network(cidr)
        if current['location']!='eastasia' or props.get('virtualNetworkPeerings') or (props.get('dhcpOptions') or {}).get('dnsServers'):raise ValueError('Network topology differs')
        if not any(candidate.subnet_of(ipaddress.ip_network(prefix)) for prefix in props['addressSpace']['addressPrefixes']):raise ValueError('Subnet outside VNet')
        for subnet in props['subnets']:
            for prefix in ([subnet['properties']['addressPrefix']] if subnet['properties'].get('addressPrefix') else subnet['properties'].get('addressPrefixes',[])):
                if candidate.overlaps(ipaddress.ip_network(prefix)):raise ValueError('Existing subnet overlap')
        baselines[key]=current
        nsg='ibkr-upgrade-pe-'+key+'-nsg';subnetname='private-endpoints-upgrade'
        nsgid=BASE+'Microsoft.Network/networkSecurityGroups/'+nsg
        subnetid=BASE+'Microsoft.Network/virtualNetworks/'+vnet+'/subnets/'+subnetname
        if request('GET',nsgid+'?api-version='+NETWORK_API,missing=True) is not None:raise ValueError('Existing NSG must not be overwritten')
        resources.append({'type':'Microsoft.Network/networkSecurityGroups','apiVersion':NETWORK_API,'name':nsg,'location':'eastasia','properties':{'securityRules':[
            {'name':'ApprovedCallersTls','properties':{'priority':100,'direction':'Inbound','access':'Allow','protocol':'Tcp','sourcePortRange':'*','destinationPortRange':'443','sourceAddressPrefixes':callers,'destinationAddressPrefix':cidr}},
            {'name':'DenyOtherInbound','properties':{'priority':200,'direction':'Inbound','access':'Deny','protocol':'*','sourcePortRange':'*','destinationPortRange':'*','sourceAddressPrefix':'*','destinationAddressPrefix':'*'}}]}})
        resources.append({'type':'Microsoft.Network/virtualNetworks/subnets','apiVersion':NETWORK_API,'name':vnet+'/'+subnetname,'dependsOn':[nsgid],'properties':{'addressPrefix':cidr,'networkSecurityGroup':{'id':nsgid},'privateEndpointNetworkPolicies':'Enabled','privateLinkServiceNetworkPolicies':'Enabled'}})
        expected.extend([nsgid,subnetid])
    for key,kind,name,group,api in TARGETS:
        targetid=BASE+kind+'/'+name;target=request('GET',targetid+'?api-version='+api)
        if target['location']!='eastasia' or group=='registry' and target['sku']['name']!='Premium':raise ValueError('East Asia Premium registry prerequisite')
        if target['properties'].get('privateEndpointConnections'):raise ValueError('Existing endpoints require fresh reconciliation')
        # Registry and storage accounts can share a short name. Bind baselines
        # to the full ARM resource identity, never to that ambiguous short name.
        targets[targetid]=target
        endpoint='ibkr-upgrade-'+key+'-'+group+('-flex' if name.startswith('ibkrv2flex') else '')+'-pe'
        endpointid=BASE+'Microsoft.Network/privateEndpoints/'+endpoint
        if request('GET',endpointid+'?api-version='+NETWORK_API,missing=True) is not None:raise ValueError('Endpoint already exists')
        subnetid=BASE+'Microsoft.Network/virtualNetworks/'+NETWORKS[key][0]+'/subnets/private-endpoints-upgrade'
        resources.append({'type':'Microsoft.Network/privateEndpoints','apiVersion':NETWORK_API,'name':endpoint,'location':'eastasia','dependsOn':[subnetid],'properties':{'subnet':{'id':subnetid},'privateLinkServiceConnections':[{'name':endpoint+'-connection','properties':{'privateLinkServiceId':targetid,'groupIds':[group]}}]}})
        expected.append(endpointid)
    template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':resources}
    validate_target_baselines(targets)
    OUT.mkdir()
    save('template',template)
    plan={'scope':'Additive East Asia private endpoints and dedicated TLS-restricted subnets only','vnets':baselines,'targets':targets,'expected_created_resource_ids':expected,'dns_links_created':False,'public_access_changed':False,'peering_changed':False,'private_access_qualified':False,'rollback':'No callers are moved in this stage. If new resources fail qualification, leave existing public/DNS paths unchanged; preserve and investigate new resources without automatic deletion. DNS/cutover is separately gated.','template':template}
    save('plan',{**plan,'protected_receipt':retain(plan,'upgrade-additive-endpoints-plan')})
    print(json.dumps({'prepared':True,'new_resources':len(resources),'private_endpoints':len(TARGETS),'live_changes':False,'dns_or_public_cutover':False}))
if __name__=='__main__':main()
