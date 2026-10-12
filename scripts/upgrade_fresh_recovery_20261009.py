"""Add a private East Asia recovery subnet and a fresh PITR clone; no live redirect."""
import ipaddress,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_metadata_monitor import BASE
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage8/fresh-recovery'
API='?api-version=2024-08-01'; NET='?api-version=2024-05-01'
SOURCE=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv2stg-pg-auhecwc4itaoo')
TARGET=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv2stg-upgrade-recovery-1009')
VNET=rid('Microsoft.Network/virtualNetworks','ibkrv2stg-vnet')
SUBNET=VNET+'/subnets/upgrade-recovery'
NSG=rid('Microsoft.Network/networkSecurityGroups','ibkrv2stg-upgrade-recovery-nsg')
PREFIX='10.50.11.0/27'
CALLERS=['10.50.0.0/23','10.50.9.0/27']

def save(name,value):
    ROOT.mkdir(exist_ok=True)
    receipt=retain(value,'upgrade-fresh-recovery-'+name)
    with (ROOT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':receipt},f,indent=2)
def read(name):
    value=json.loads((ROOT/(name+'.json')).read_bytes())
    remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected record differs')
    return value
def boundary(server):
    return {k:server.get(k) for k in ('id','location','sku','identity')}|{'properties':{k:server['properties'].get(k) for k in ('network','highAvailability','storage','version','backup')}}
def vnet_boundary(vnet):
    keys=('addressPrefix','addressPrefixes','delegations','networkSecurityGroup','routeTable','natGateway','serviceEndpoints','privateEndpointNetworkPolicies','privateLinkServiceNetworkPolicies')
    return {'space':vnet['properties']['addressSpace'],'dns':vnet['properties'].get('dhcpOptions'),
      'subnets':{s['name']:{k:s['properties'].get(k) for k in keys} for s in vnet['properties']['subnets'] if s['name']!='upgrade-recovery'}}
def configuration(value):
    # A child subnet creation changes the parent ETag on unchanged delegations.
    # Preserve and compare every configuration field, excluding only that version stamp.
    if isinstance(value,dict):return {k:configuration(v) for k,v in value.items() if k!='etag'}
    if isinstance(value,list):return [configuration(v) for v in value]
    return value
def validate_network(vnet):
    proposed=ipaddress.ip_network(PREFIX)
    existing=[ipaddress.ip_network(p) for s in vnet['properties']['subnets'] for p in ([s['properties']['addressPrefix']] if s['properties'].get('addressPrefix') else s['properties'].get('addressPrefixes',[]))]
    if any(proposed.overlaps(p) for p in existing):raise ValueError('Recovery prefix overlaps')
    if not any(proposed.subnet_of(ipaddress.ip_network(p)) for p in vnet['properties']['addressSpace']['addressPrefixes']):raise ValueError('Prefix outside approved VNet')
def main(stage):
    source=request('GET',SOURCE+API);vnet=request('GET',VNET+NET)
    if stage=='prepare':
        if ROOT.exists():raise ValueError('Preserve prior recovery plan')
        if source['location'].replace(' ','').lower()!='eastasia' or source['properties']['state']!='Ready':raise ValueError('East Asia ready source required')
        for target,api in ((TARGET,API),(SUBNET,NET),(NSG,NET)):
            if request('GET',target+api,missing=True) is not None:raise ValueError('Recovery resource exists')
        validate_network(vnet)
        point=(datetime.now(timezone.utc)-timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        network={**source['properties']['network'],'delegatedSubnetResourceId':SUBNET,'publicNetworkAccess':'Disabled'}
        body={'location':'eastasia','tags':{'purpose':'isolated-upgrade-recovery','owner':'portfolio-platform'},'properties':{'createMode':'PointInTimeRestore','sourceServerResourceId':SOURCE,'pointInTimeUTC':point,'availabilityZone':'2','network':network}}
        rules=[{'name':'AllowApprovedCallers','properties':{'priority':100,'direction':'Inbound','access':'Allow','protocol':'Tcp','sourcePortRange':'*','destinationPortRange':'5432','sourceAddressPrefixes':[*CALLERS,PREFIX],'destinationAddressPrefix':PREFIX}},
          {'name':'DenyOtherInbound','properties':{'priority':4000,'direction':'Inbound','access':'Deny','protocol':'*','sourcePortRange':'*','destinationPortRange':'*','sourceAddressPrefix':'*','destinationAddressPrefix':'*'}}]
        save('plan',{'source':boundary(source),'vnet':vnet_boundary(vnet),'restore_body':body,
          'nsg_body':{'location':'eastasia','properties':{'securityRules':rules}},
          'subnet_body':{'properties':{'addressPrefix':PREFIX,'networkSecurityGroup':{'id':NSG},'delegations':[{'name':'postgres','properties':{'serviceName':'Microsoft.DBforPostgreSQL/flexibleServers'}}]}},
          'authorization':'Owner requested full upgrade; recovery limited to East Asia. New isolated clone only; retain existing recovery servers.',
          'live_database_redirect':False,'public_access_enabled':False,'credential_changes':False})
        print(json.dumps({'prepared':True,'point':point,'target':TARGET}));return
    plan=read('plan')
    if boundary(source)!=plan['source'] or configuration(vnet_boundary(vnet))!=configuration(plan['vnet']):raise ValueError('Source or existing network drift')
    if stage in ('nsg','subnet','restore'):
        target,api,body={'nsg':(NSG,NET,plan['nsg_body']),'subnet':(SUBNET,NET,plan['subnet_body']),'restore':(TARGET,API,plan['restore_body'])}[stage]
        if (ROOT/(stage+'-intent.json')).exists() or request('GET',target+api,missing=True) is not None:raise ValueError('Existing operation; inspect instead of retry')
        if stage=='subnet':
            if request('GET',NSG+NET)['properties']['provisioningState']!='Succeeded':raise ValueError('NSG not ready')
        if stage=='restore':
            subnet=request('GET',SUBNET+NET)
            if subnet['properties']['provisioningState']!='Succeeded' or subnet['properties']['addressPrefix']!=PREFIX or subnet['properties']['networkSecurityGroup']['id'].lower()!=NSG.lower():raise ValueError('Recovery subnet differs')
        save(stage+'-intent',{'target':target,'body':body,'dispatched_at':datetime.now(timezone.utc).isoformat()})
        ack=request('PUT',target+api,data=body)
        save(stage+'-ack',{'id':ack.get('id'),'state':ack.get('properties',{}).get('provisioningState',ack.get('properties',{}).get('state'))})
        print(json.dumps({'submitted':stage,'production_redirect':False}));return
    if stage!='state':raise ValueError('Unknown stage')
    target=request('GET',TARGET+API,missing=True)
    if target:
        props=target['properties'];expected=plan['restore_body']['properties']['network']
        if target['location'].replace(' ','').lower()!='eastasia' or props['network']!=expected:raise ValueError('Recovery isolation differs')
        print(json.dumps({'target':target['name'],'state':props['state'],'network_private':True,'ha':props.get('highAvailability'),'source_unchanged':True}))
    else:print(json.dumps({'target_exists':False}))
if __name__=='__main__':
    if sys.argv[2:]==['legacy']:
        ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/legacy-recovery-v1'
        SOURCE=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv11-pg-5rcc62xxqblay')
        TARGET=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv11-upgrade-recovery-1009')
        VNET=rid('Microsoft.Network/virtualNetworks','ibkrv11-vnet');SUBNET=VNET+'/subnets/upgrade-recovery'
        NSG=rid('Microsoft.Network/networkSecurityGroups','ibkrv11-upgrade-recovery-nsg')
        PREFIX='10.40.11.0/27';CALLERS=['10.40.0.0/23','10.40.16.0/24']
    elif sys.argv[2:]:raise ValueError('Unknown recovery scope')
    main(sys.argv[1])
