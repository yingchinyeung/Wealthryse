"""Create-only private DNS preparation entirely inside the authorized RG.

Standard main-network service zones plus exact-name legacy child zones provide
separate address views without peering or an additional resource-group grant.
All zones remain unlinked. Legacy apex A records are explicitly drift-checked.
"""
import json,sys,hashlib,subprocess,os
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_metadata_monitor import BASE
from prepare_upgrade_private_dns_20261009 import endpoints,SUFFIX,DNS_API,NET_API
from preview_upgrade_private_endpoints_20261009 import validate_changes
from audit_azure_runtime import AZ,RG
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

OUT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/unlinked-dns-v2'
DEPLOYMENT=BASE+'Microsoft.Resources/deployments/ibkr-upgrade-unlinked-dns-20261009';DEPLOY_API='?api-version=2022-09-01'

def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-scoped-dns-'+name)},f,indent=2)
def load(name):
    value=json.loads((OUT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected DNS evidence differs')
    return value
def compose(discovered):
    zones={};resources=[]
    for item in discovered.values():
        for label,addresses in item['records'].items():
            name=SUFFIX[item['kind']] if item['network']=='main' else label+'.'+SUFFIX[item['kind']]
            record=label if item['network']=='main' else '@'
            z=zones.setdefault(name,{'network':item['network'],'records':{}})
            if z['network']!=item['network'] or record in z['records']:raise ValueError('DNS record collision')
            z['records'][record]=addresses
    if len(zones)!=7:raise ValueError('Seven disjoint DNS zone scopes required')
    for name,z in zones.items():
        resources.append({'type':'Microsoft.Network/privateDnsZones','apiVersion':'2020-06-01','name':name,'location':'global','tags':{'application':'ibkr-portfolio-platform','network-scope':z['network'],'purpose':'private-endpoint-dns'}})
        if z['network']=='legacy':
            resources.append({'type':'Microsoft.Network/privateDnsZones/A','apiVersion':'2020-06-01','name':name+'/@','dependsOn':[BASE+'Microsoft.Network/privateDnsZones/'+name],'properties':{'ttl':30,'aRecords':[{'ipv4Address':ip} for ip in z['records']['@']]}})
    for name,item in discovered.items():
        if item['network']!='main':continue
        zone=BASE+'Microsoft.Network/privateDnsZones/'+SUFFIX[item['kind']]
        resources.append({'type':'Microsoft.Network/privateEndpoints/privateDnsZoneGroups','apiVersion':'2024-05-01','name':name+'/upgrade','dependsOn':[zone],'properties':{'privateDnsZoneConfigs':[{'name':'service-zone','properties':{'privateDnsZoneId':zone}}]}})
    return zones,{'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':resources}
def resource_id(resource):
    kind=resource['type'].split('/');names=resource['name'].split('/')
    if len(kind)!=len(names)+1:raise ValueError('Resource hierarchy differs')
    return BASE+kind[0]+'/'+('/'.join(x for pair in zip(kind[1:],names) for x in pair))
def main(stage):
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing DNS plan')
        discovered=endpoints();zones,template=compose(discovered)
        ids=[resource_id(r) for r in template['resources']]
        for resource,path in zip(template['resources'],ids):
            if request('GET',path+'?api-version='+resource['apiVersion'],missing=True) is not None:raise ValueError('Never overwrite existing DNS resource')
        for item in discovered.values():
            if request('GET',item['id']+'/privateDnsZoneGroups'+NET_API)['value']:raise ValueError('Existing endpoint DNS association')
        OUT.mkdir()
        with (OUT/'template.json').open('x',encoding='utf-8') as f:json.dump(template,f,indent=2)
        save('plan',{'endpoints':discovered,'zones':zones,'template':template,'expected_ids':ids,'scope':'Three standard main-network zones and four exact-name legacy child zones; six main endpoint associations and four legacy apex A records. All unlinked, same authorized resource group, no peering or public-access change.','design_reason':'Separate DNS resource-group access was denied. Directly linked private child domains are supported; use exact legacy child zones inside the existing authorized RG instead. No additional permission is sought or granted.','maintenance_requirement':'Legacy apex records are bound to endpoint IPs and must pass drift checks before DNS cutover, public restriction and future endpoint changes. No claim of automatic zone-group updates for legacy records.','documentation':['https://learn.microsoft.com/en-us/azure/dns/private-dns-privatednszone','https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration'],'dns_cutover':False,'public_access_changed':False})
        print('Protected seven unlinked same-RG DNS zones; no network access expansion');return
    plan=load('plan')
    if json.loads((OUT/'template.json').read_bytes())!=plan['template']:raise ValueError('Template changed')
    if stage in ('preview','apply'):
        if endpoints()!=plan['endpoints']:raise ValueError('Private endpoint binding drift')
    if stage=='preview':
        if (OUT/'preview.json').exists():raise ValueError('Preserve existing preview')
        r=subprocess.run([AZ,'-m','azure.cli','deployment','group','what-if','--resource-group',RG,'--template-file',str(OUT/'template.json'),'--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,encoding='utf-8',timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if r.returncode:raise ValueError('Create-only preview failed')
        value=json.loads(r.stdout);save('preview',value)
        if value.get('status')!='Succeeded':raise ValueError('Preview incomplete')
        validate_changes(value['changes'],plan['expected_ids'])
        save('preview-qualified',{'passed':True,'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest(),'resource_count':len(plan['expected_ids'])})
        print('Exact seventeen-resource create-only DNS preview passed');return
    if stage=='apply':
        preview=load('preview-qualified')
        if not preview['passed'] or preview['plan_sha256']!=hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest():raise ValueError('Qualified preview required')
        if (OUT/'apply-intent.json').exists() or request('GET',DEPLOYMENT+DEPLOY_API,missing=True) is not None:raise ValueError('Reconcile existing deployment')
        for resource,path in zip(plan['template']['resources'],plan['expected_ids']):
            if request('GET',path+'?api-version='+resource['apiVersion'],missing=True) is not None:raise ValueError('Do not overwrite DNS state')
        body={'properties':{'mode':'Incremental','template':plan['template']}}
        save('apply-intent',{'body':body,'resource':DEPLOYMENT})
        r=request('PUT',DEPLOYMENT+DEPLOY_API,data=body);save('apply-ack',{'id':r['id'],'state':r['properties'].get('provisioningState')})
        print('Submitted unlinked DNS resources; no active DNS/public/peering change');return
    if stage!='verify':raise ValueError('Unknown stage')
    deployed=request('GET',DEPLOYMENT+DEPLOY_API)
    if deployed['properties']['provisioningState']!='Succeeded':print(json.dumps({'state':deployed['properties']['provisioningState']}));return
    for name,zone in plan['zones'].items():
        path=BASE+'Microsoft.Network/privateDnsZones/'+name
        if request('GET',path+'/virtualNetworkLinks'+DNS_API)['value']:raise ValueError('Unexpected active DNS link')
        answer=request('GET',path+'/A'+DNS_API)
        if answer.get('nextLink'):raise ValueError('Unexpected record pagination')
        actual={r['name']:sorted(x['ipv4Address'] for x in r['properties']['aRecords']) for r in answer['value']}
        if actual!=zone['records']:raise ValueError('Private DNS record mismatch: '+name)
    for item in plan['endpoints'].values():
        live=request('GET',item['id']+NET_API)
        if live['properties']['subnet']!=item['subnet']:raise ValueError('Endpoint moved')
        addresses=[]
        for nic in live['properties']['networkInterfaces']:
            addresses.extend(c['properties']['privateIPAddress'] for c in request('GET',nic['id']+NET_API)['properties']['ipConfigurations'])
        if sorted(addresses)!=sorted(ip for ips in item['records'].values() for ip in ips):raise ValueError('Endpoint IP drift')
    save('verified',{'passed':True,'zones':plan['zones'],'A_records':sum(len(z['records']) for z in plan['zones'].values()),'dns_cutover':False,'public_access_changed':False,'peering_changed':False,'resource_group':RG})
    print('Seven unlinked zones and twelve private records verified; no callers redirected')
if __name__=='__main__':main(sys.argv[1])
