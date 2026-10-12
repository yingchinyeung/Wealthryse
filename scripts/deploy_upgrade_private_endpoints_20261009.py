"""Deploy reviewed additive resources; never changes DNS or public access settings."""
import copy,hashlib,json,sys
from deploy_metadata_monitor import request,BASE,SUB
from audit_azure_runtime import RG
from prepare_upgrade_private_endpoints_20261009 import OUT,NETWORK_API,TARGETS,NETWORKS,validate_target_baselines
from preview_upgrade_private_endpoints_20261009 import validate_changes
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
PATH=BASE+'Microsoft.Resources/deployments/ibkr-upgrade-private-additive-20261009'
API='?api-version=2022-09-01'
def read(name):
    value=json.loads((OUT/(name+'.json')).read_bytes())
    remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected '+name+' differs')
    return value
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-private-endpoint-'+name)},f,indent=2)
def stable(value):
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('etag','provisioningState','resourceGuid')}
    if isinstance(value,list):return [stable(v) for v in value]
    return value
def target_boundary(value):
    return {'location':value['location'],'sku':value.get('sku'),'identity':value.get('identity'),'properties':{k:v for k,v in value['properties'].items() if k not in ('privateEndpointConnections','provisioningState','dataEndpointEnabled','dataEndpointHostNames')}}
def contains_requested(actual,wanted,key=''):
    if isinstance(wanted,dict):return isinstance(actual,dict) and all(k in actual and contains_requested(actual[k],v,k) for k,v in wanted.items())
    if isinstance(wanted,list):return isinstance(actual,list) and len(actual)==len(wanted) and all(contains_requested(a,b,key) for a,b in zip(actual,wanted))
    if key.lower().endswith('id') and isinstance(actual,str) and isinstance(wanted,str):return actual.lower()==wanted.lower()
    return actual==wanted
def main(stage):
    plan=read('plan');preview=read('what-if-qualified')
    validate_target_baselines(plan['targets'])
    if preview['plan_sha256']!=hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest() or not preview['passed']:raise ValueError('Preview binding failed')
    validate_changes(preview['preview']['changes'],plan['expected_created_resource_ids'])
    if json.loads((OUT/'template.json').read_bytes())!=plan['template']:raise ValueError('Template changed')
    for key,(vnet,_,_) in NETWORKS.items():
        current=request('GET',BASE+'Microsoft.Network/virtualNetworks/'+vnet+'?api-version='+NETWORK_API)
        before=copy.deepcopy(plan['vnets'][key]);projection=copy.deepcopy(current)
        if stage=='verify':projection['properties']['subnets']=[s for s in projection['properties']['subnets'] if s['name']!='private-endpoints-upgrade']
        if stable(projection['properties'])!=stable(before['properties']):raise ValueError('Existing VNet settings changed')
    for _,kind,name,_,api in TARGETS:
        current=request('GET',BASE+kind+'/'+name+'?api-version='+api)
        baseline=plan['targets'][BASE+kind+'/'+name]
        if current['id'].lower()!=baseline['id'].lower():raise ValueError('Resource identity differs')
        if stable(target_boundary(current))!=stable(target_boundary(baseline)):raise ValueError('Existing service configuration changed: '+name)
    if stage=='apply':
        if (OUT/'apply-intent.json').exists() or request('GET',PATH+API,missing=True) is not None:raise ValueError('Reconcile existing deployment')
        for rid in plan['expected_created_resource_ids']:
            if request('GET',rid+'?api-version='+NETWORK_API,missing=True) is not None:raise ValueError('Target already exists; do not overwrite')
        body={'properties':{'mode':'Incremental','template':plan['template']}}
        save('apply-intent',{'body':body,'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest(),'authorization':'Owner requested full upgrade, approved private administration, unlimited reasonable repair cost and East Asia-only recovery. This stage only adds isolated private endpoints, with no DNS/public switch.','public_access_changed':False,'dns_links_created':False})
        ack=request('PUT',PATH+API,data=body)
        save('apply-ack',{'id':ack.get('id'),'state':ack.get('properties',{}).get('provisioningState')})
        print('Additive endpoint deployment submitted; public access and DNS unchanged');return
    if stage!='verify':raise ValueError('Unknown stage')
    intent=read('apply-intent')
    if intent['body']['properties']['template']!=plan['template']:raise ValueError('Dispatched template differs')
    deployment=request('GET',PATH+API)
    if deployment['properties']['provisioningState']!='Succeeded':
        print(json.dumps({'state':deployment['properties']['provisioningState'],'qualified':False}));return
    endpoints=[]
    for resource in plan['template']['resources']:
        rid=BASE+resource['type']+'/'+resource['name']
        if resource['type']=='Microsoft.Network/virtualNetworks/subnets':
            vnet,subnet=resource['name'].split('/');rid=BASE+'Microsoft.Network/virtualNetworks/'+vnet+'/subnets/'+subnet
        live=request('GET',rid+'?api-version='+NETWORK_API)
        if live['properties'].get('provisioningState')!='Succeeded':raise ValueError('New resource not ready')
        if not contains_requested(live['properties'],resource['properties']):raise ValueError('New resource properties differ from approved template')
        if resource['type']=='Microsoft.Network/privateEndpoints':
            p=live['properties'];connections=p['privateLinkServiceConnections']
            if len(connections)!=1 or connections[0]['properties']['privateLinkServiceConnectionState']['status']!='Approved' or p['subnet']['id'].lower()!=resource['properties']['subnet']['id'].lower():raise ValueError('Endpoint connection differs')
            if request('GET',rid+'/privateDnsZoneGroups?api-version='+NETWORK_API)['value']:raise ValueError('Unexpected DNS zone group')
            endpoints.append({'id':rid,'dns':p.get('customDnsConfigs'),'network_interfaces':p.get('networkInterfaces'),'connection':connections[0]['properties']['privateLinkServiceId']})
    save('verified',{'passed':True,'new_resources':11,'endpoints':endpoints,'existing_service_settings_preserved':True,'existing_vnet_settings_preserved':True,'public_access_changed':False,'dns_changed':False,'private_caller_acceptance_complete':False,'full_upgrade_complete':False})
    print('Additive resources verified; no DNS/public cutover and no private-caller acceptance claimed')
if __name__=='__main__':main(sys.argv[1])
