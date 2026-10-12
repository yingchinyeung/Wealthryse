"""Unlinked, isolated private DNS zones and endpoint groups; no DNS cutover.

Main and legacy VNet DNS must not publish each other's unreachable addresses.
The additional resource group contains only DNS metadata, never portfolio data.
"""
import json,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid,SUB
from deploy_metadata_monitor import BASE
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

OUT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/unlinked-dns-v1'
GROUP='/subscriptions/'+SUB+'/resourceGroups/rg-ibkr-dns-main-ea'
DNS_API='?api-version=2020-06-01';NET_API='?api-version=2024-05-01'
SUFFIX={'registry':'privatelink.azurecr.io','vault':'privatelink.vaultcore.azure.net','blob':'privatelink.blob.core.windows.net'}
ENDPOINTS={
 'ibkr-upgrade-main-registry-pe':('main','registry'),
 'ibkr-upgrade-main-vault-pe':('main','vault'),
 'ibkr-upgrade-main-vault-flex-pe':('main','vault'),
 'ibkr-upgrade-main-blob-pe':('main','blob'),
 'ibkr-upgrade-main-legacy-registry-pe':('main','registry'),
 'ibkr-upgrade-main-legacy-blob-pe':('main','blob'),
 'ibkr-upgrade-legacy-registry-pe':('legacy','registry'),
 'ibkr-upgrade-legacy-vault-pe':('legacy','vault'),
 'ibkr-upgrade-legacy-blob-pe':('legacy','blob')}

def zone_id(network,kind):
    base=GROUP+'/providers/' if network=='main' else BASE
    if network not in ('main','legacy') or kind not in SUFFIX:raise ValueError('Unknown zone scope')
    return base+'Microsoft.Network/privateDnsZones/'+SUFFIX[kind]
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-unlinked-dns-'+name)},f,indent=2)
def load(name):
    value=json.loads((OUT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected DNS evidence differs')
    return value
def records_for(dns,kind):
    records={}
    for item in dns:
        host=item['fqdn']
        suffix={'registry':'.azurecr.io','vault':'.vault.azure.net','blob':'.blob.core.windows.net'}[kind]
        if not host.endswith(suffix):raise ValueError('Unexpected private hostname')
        name=host[:-len(suffix)]
        if name in records:raise ValueError('Duplicate hostname')
        records[name]=sorted(item['ipAddresses'])
    return records
def endpoints():
    result={}
    for name,(network,kind) in ENDPOINTS.items():
        path=rid('Microsoft.Network/privateEndpoints',name);r=request('GET',path+NET_API);p=r['properties']
        if r['location']!='eastasia' or p['provisioningState']!='Succeeded' or any(c['properties']['privateLinkServiceConnectionState']['status']!='Approved' for c in p['privateLinkServiceConnections']):raise ValueError('Endpoint not ready')
        prefix='10.50.10.' if network=='main' else '10.40.10.'
        if not p['customDnsConfigs'] or any(not address.startswith(prefix) for item in p['customDnsConfigs'] for address in item['ipAddresses']):raise ValueError('Private endpoint network differs')
        result[name]={'id':path,'network':network,'kind':kind,'dns':p['customDnsConfigs'],'subnet':p['subnet'],'records':records_for(p['customDnsConfigs'],kind)}
    return result
def create_once(name,path,api,body):
    if (OUT/(name+'-intent.json')).exists() or request('GET',path+api,missing=True) is not None:raise ValueError('Reconcile existing '+name+'; never overwrite')
    save(name+'-intent',{'resource':path,'body':body})
    r=request('PUT',path+api,data=body);save(name+'-ack',{'id':r['id']})
def main(stage):
    if stage=='plan':
        if OUT.exists() or request('GET',GROUP+'?api-version=2021-04-01',missing=True) is not None:raise ValueError('Preserve existing DNS group/plan')
        discovered=endpoints()
        for network in ('main','legacy'):
            for kind in SUFFIX:
                if request('GET',zone_id(network,kind)+DNS_API,missing=True) is not None:raise ValueError('Never overwrite existing DNS zone')
        for item in discovered.values():
            if request('GET',item['id']+'/privateDnsZoneGroups'+NET_API)['value']:raise ValueError('Existing endpoint DNS group')
        OUT.mkdir()
        save('plan',{'endpoints':discovered,'main_dns_group':GROUP,'scope':'Six initially unlinked service DNS zones; main and legacy zones remain separate. Nine endpoint DNS groups. No VNet links, public restrictions, peering or PostgreSQL DNS changes.','authorization':'Owner approved full private-network upgrade and required secure existing dependencies. Additional East Asia resource group holds DNS metadata only to preserve network isolation.','documentation':'https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration','dns_cutover':False,'portfolio_data_copied':False})
        print('Protected six unlinked zones and nine DNS groups; no active DNS change');return
    plan=load('plan')
    if endpoints()!=plan['endpoints']:raise ValueError('Endpoint addresses or network drift')
    if stage=='group':
        create_once('resource-group',GROUP,'?api-version=2021-04-01',{'location':'eastasia','tags':{'application':'ibkr-portfolio-platform','purpose':'isolated-main-private-dns','owner':'portfolio-platform'}})
        print('Created DNS-metadata-only resource group in East Asia');return
    if stage=='zones':
        group=request('GET',GROUP+'?api-version=2021-04-01')
        if group['location']!='eastasia' or group.get('tags',{}).get('purpose')!='isolated-main-private-dns':raise ValueError('DNS group identity differs')
        for network in ('main','legacy'):
            for kind in SUFFIX:
                create_once('zone-'+network+'-'+kind,zone_id(network,kind),DNS_API,{'location':'global','tags':{'application':'ibkr-portfolio-platform','network-scope':network,'purpose':'private-endpoint-dns'}})
        print('Created six unlinked private DNS zones; no callers redirected');return
    if stage=='associate':
        for name,item in plan['endpoints'].items():
            zone=zone_id(item['network'],item['kind'])
            if request('GET',zone+'/virtualNetworkLinks'+DNS_API)['value']:raise ValueError('Do not associate after active DNS cutover')
            body={'properties':{'privateDnsZoneConfigs':[{'name':'service-zone','properties':{'privateDnsZoneId':zone}}]}}
            create_once('association-'+name,item['id']+'/privateDnsZoneGroups/upgrade',NET_API,body)
        print('Associated nine endpoints with unlinked DNS zones; no callers redirected');return
    if stage!='verify':raise ValueError('Unknown stage')
    accepted=[]
    for network in ('main','legacy'):
        for kind in SUFFIX:
            zone=zone_id(network,kind)
            if request('GET',zone+'/virtualNetworkLinks'+DNS_API)['value']:raise ValueError('Unexpected VNet link')
            expected={}
            for item in plan['endpoints'].values():
                if item['network']==network and item['kind']==kind:
                    if expected.keys()&item['records'].keys():raise ValueError('Conflicting endpoint record')
                    expected.update(item['records'])
            response=request('GET',zone+'/A'+DNS_API)
            if response.get('nextLink'):raise ValueError('Unexpected record pagination')
            actual={r['name']:sorted(x['ipv4Address'] for x in r['properties']['aRecords']) for r in response['value']}
            if actual!=expected:raise ValueError('Automatic private DNS records differ')
            accepted.append({'zone':zone,'records':actual,'vnet_links':0})
    save('verified',{'passed':True,'zones':accepted,'endpoint_associations':9,'dns_cutover':False,'public_access_changed':False,'peering_changed':False,'portfolio_data_copied':False})
    print(json.dumps({'passed':True,'zones':len(accepted),'A_records':sum(len(z['records']) for z in accepted),'vnet_links':0}))
if __name__=='__main__':main(sys.argv[1])
