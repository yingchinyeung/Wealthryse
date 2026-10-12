"""Add two main-network endpoints for existing legacy storage/build dependencies.

No DNS links, peering, service public settings or existing endpoints are changed.
"""
import json,sys,subprocess,os,hashlib
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_upgrade_private_endpoints_20261009 import stable,target_boundary,contains_requested
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from preview_upgrade_private_endpoints_20261009 import validate_changes
from audit_azure_runtime import AZ,RG

OUT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/legacy-main-paths-v1'
VNET=rid('Microsoft.Network/virtualNetworks','ibkrv2stg-vnet')
SUBNET=VNET+'/subnets/private-endpoints-upgrade'
API='?api-version=2024-05-01'
TARGETS={'blob':(rid('Microsoft.Storage/storageAccounts','ibkrv115rcc62xxqblay'),'?api-version=2023-05-01'),'registry':(rid('Microsoft.ContainerRegistry/registries','ibkrv115rcc62xxqblay'),'?api-version=2023-07-01')}
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-main-legacy-'+name)},f,indent=2)
def load(name):
    value=json.loads((OUT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected plan differs')
    return value
def endpoint(kind):return rid('Microsoft.Network/privateEndpoints','ibkr-upgrade-main-legacy-'+kind+'-pe')
def boundary():
    v=request('GET',VNET+API)
    if v['location']!='eastasia' or v['properties'].get('virtualNetworkPeerings'):raise ValueError('Preserve isolated East Asia topology')
    s=request('GET',SUBNET+API)
    if s['properties']['addressPrefix']!='10.50.10.0/27' or s['properties']['privateEndpointNetworkPolicies']!='Enabled':raise ValueError('Existing endpoint subnet differs')
    return {'vnet':{'id':v['id'],'dns':v['properties'].get('dhcpOptions'),'address_space':v['properties']['addressSpace'],'peerings':v['properties'].get('virtualNetworkPeerings',[])},'subnet':{'id':s['id'],'properties':{k:stable(s['properties'].get(k)) for k in ('addressPrefix','networkSecurityGroup','routeTable','privateEndpointNetworkPolicies','privateLinkServiceNetworkPolicies')}}}
def main(stage,kind=None):
    current=boundary()
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing endpoint plan')
        targets={};bodies={}
        for key,(target,api) in TARGETS.items():
            value=request('GET',target+api)
            if value['location']!='eastasia' or key=='registry' and value['sku']['name']!='Premium':raise ValueError('East Asia Premium prerequisite')
            if request('GET',endpoint(key)+API,missing=True) is not None:raise ValueError('Do not overwrite endpoint')
            targets[key]=stable(target_boundary(value))
            name=endpoint(key).rsplit('/',1)[-1]
            bodies[key]={'location':'eastasia','properties':{'subnet':{'id':SUBNET},'privateLinkServiceConnections':[{'name':name+'-connection','properties':{'privateLinkServiceId':target,'groupIds':[key]}}]}}
        OUT.mkdir(parents=True)
        save('plan',{'network_boundary':current,'targets':targets,'bodies':bodies,'authorization':'Full approved private-network/build upgrade; preserve existing legacy storage reads without VNet peering','dns_links_created':False,'public_access_changed':False,'rollback':'These additive endpoints have no redirected callers. Preserve and investigate on failure; never delete existing endpoints or weaken authentication.'})
        print('Protected two additive private endpoints; no DNS/public/peering cutover');return
    if stage=='preview':
        plan=load('plan')
        if current!=plan['network_boundary'] or (OUT/'what-if.json').exists():raise ValueError('Boundary drift or existing preview')
        template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':[{'type':'Microsoft.Network/privateEndpoints','apiVersion':'2024-05-01','name':endpoint(k).rsplit('/',1)[-1],**body} for k,body in plan['bodies'].items()]}
        with (OUT/'template.json').open('x',encoding='utf-8') as f:json.dump(template,f,indent=2)
        r=subprocess.run([AZ,'-m','azure.cli','deployment','group','what-if','--resource-group',RG,'--template-file',str(OUT/'template.json'),'--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,encoding='utf-8',timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if r.returncode:raise ValueError('Create-only preview failed; no resources deployed')
        result=json.loads(r.stdout);save('what-if',result)
        if result.get('status')!='Succeeded':raise ValueError('Preview incomplete')
        validate_changes(result['changes'],[endpoint(k) for k in TARGETS])
        save('preview-qualified',{'passed':True,'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest(),'only_two_additive_endpoints':True})
        print('Exact two-resource create-only preview passed');return
    if kind not in TARGETS:raise ValueError('Unknown endpoint')
    plan=load('plan');target,api=TARGETS[kind]
    if current!=plan['network_boundary'] or stable(target_boundary(request('GET',target+api)))!=plan['targets'][kind]:raise ValueError('Network or target settings drift')
    existing=request('GET',endpoint(kind)+API,missing=True)
    if stage=='apply':
        preview=load('preview-qualified')
        if not preview['passed'] or preview['plan_sha256']!=hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest():raise ValueError('Protected create-only preview required')
        if existing is not None or (OUT/(kind+'-intent.json')).exists():raise ValueError('Reconcile existing endpoint creation')
        save(kind+'-intent',{'resource':endpoint(kind),'body':plan['bodies'][kind]})
        result=request('PUT',endpoint(kind)+API,data=plan['bodies'][kind]);save(kind+'-ack',{'id':result['id'],'state':result['properties'].get('provisioningState')})
        print('Submitted main-network legacy '+kind+' endpoint; no callers redirected');return
    if stage!='verify':raise ValueError('Unknown stage')
    if existing is None or existing['properties'].get('provisioningState')!='Succeeded':print('Endpoint not ready');return
    p=existing['properties']
    if not contains_requested(p,plan['bodies'][kind]['properties']):raise ValueError('Endpoint configuration differs')
    if p['privateLinkServiceConnections'][0]['properties']['privateLinkServiceConnectionState']['status']!='Approved':raise ValueError('Endpoint not approved')
    if request('GET',endpoint(kind)+'/privateDnsZoneGroups'+API)['value']:raise ValueError('Unexpected DNS association')
    save(kind+'-verified',{'passed':True,'endpoint':endpoint(kind),'dns':p['customDnsConfigs'],'network_preserved':True,'public_access_changed':False,'dns_changed':False})
    print(json.dumps({'kind':kind,'passed':True,'dns':p['customDnsConfigs']}))

if __name__=='__main__':main(*sys.argv[1:])
