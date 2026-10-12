"""Forward qualification of the failed additive access deployment, not a retry.

Preserves v2 evidence; corrects exactly two new NSG definitions. Only this task's
12 resource IDs may be created, and only its new Bastion NSG may be modified.
No existing application/service boundary, database, secret or identity is edited.
"""
import argparse,copy,hashlib,json
from pathlib import Path
import deploy_viewer_private_admin_20261008 as p
from qualify_viewer_database_recovery_20261008 import read,save
from audit_azure_runtime import az,RG

ROOT=p.ROOT/'forward-repair-v4'
NAME='viewer-private-admin-20261008-v4'
DEPLOYMENT=p.BASE+'Microsoft.Resources/deployments/'+NAME
API=p.ARM_API
SUPPORT={p.rid('Microsoft.Network/natGateways','ibkrv2stg-admin-egress'),p.rid('Microsoft.Network/publicIPAddresses','ibkrv2stg-admin-egress-pip'),p.rid('Microsoft.Network/publicIPAddresses','ibkrv2stg-bastion-pip')}

def support_scope(value):
    fields=('scope','idleTimeoutInMinutes','publicIpAddresses','ddosSettings','publicIPAllocationMethod','publicIPAddressVersion','ipAddress','ipTags')
    return {k:value.get(k) for k in ('id','type','location','sku','tags','zones')}|{'properties':{k:value['properties'].get(k) for k in fields}}

def support_baseline():
    result={}
    for path in sorted(SUPPORT):
        value=p.request('GET',path+'?api-version='+p.NET_API)
        if value['properties']['provisioningState']!='Succeeded' or value['tags'].get('purpose')!=p.TAGS['purpose']:raise ValueError('Only this task-created Ready support resources may be retained')
        result[path]=support_scope(value)
    return result

def correction(value):
    result=copy.deepcopy(value)
    for resource in result['resources']:
        if resource['name']=='ibkrv2stg-bastion-nsg':
            target=next(r for r in resource['properties']['securityRules'] if r['name']=='AllowSshRdpOutbound')
            if target['properties']['destinationAddressPrefix']!=p.ADMIN_PREFIX:raise ValueError('Unexpected prior Bastion target')
            target['properties']['destinationAddressPrefix']='VirtualNetwork'
        elif resource['name']=='ibkrv2stg-admin-nsg':
            rules=resource['properties']['securityRules']
            bad=[r for r in rules if r['name']=='AllowPlatformDns']
            if len(bad)!=1 or bad[0]['properties']['destinationAddressPrefix']!='AzurePlatformDNS':raise ValueError('Exact unsupported DNS rule required')
            # Azure's platform DNS is already allowed unless explicitly denied
            # using its dedicated service tag. That tag rejects Allow rules.
            resource['properties']['securityRules']=[r for r in rules if r['name']!='AllowPlatformDns']
    # Existing NAT/public-IP defaults (including DDoS mode and regional tier)
    # must not be deleted by replaying an incomplete create-time definition.
    # Reference those Ready resources without redeploying or editing them.
    result['resources']=[r for r in result['resources'] if r.get('scope') or p.rid(r['type'],r['name']) not in SUPPORT]
    for resource in result['resources']:resource['dependsOn']=[x for x in resource.get('dependsOn',[]) if x not in SUPPORT]
    return result

def rules_match(actual,wanted):
    left={r['name']:r['properties'] for r in actual};right={r['name']:r['properties'] for r in wanted}
    return set(left)==set(right) and all(all(left[name].get(k)==v for k,v in fields.items()) for name,fields in right.items())

def verify_new_network(value):
    for resource in value['resources']:
        if resource['type']=='Microsoft.Network/networkSecurityGroups':
            actual=p.request('GET',p.rid(resource['type'],resource['name'])+'?api-version='+p.NET_API)
            if actual['properties']['provisioningState']!='Succeeded' or not rules_match(actual['properties']['securityRules'],resource['properties']['securityRules']):raise ValueError('Accepted new NSG rules drifted')

def check_changes(changes,ids):
    allowed={x.lower() for x in ids};seen=set();bastion=p.rid('Microsoft.Network/networkSecurityGroups','ibkrv2stg-bastion-nsg').lower()
    for c in changes:
        identity=c['resourceId'].lower();kind=c['changeType']
        if kind=='Ignore' and identity.startswith(p.BASE.lower()) and c.get('delta') in (None,[]):continue
        if identity not in allowed or kind not in ('Create','NoChange','Modify'):raise ValueError('Forward repair proposes out-of-scope changes')
        if kind=='Modify' and (identity!=bastion or any(not d['path'].startswith('properties.securityRules') for d in c.get('delta',[]))):raise ValueError('Only exact newly created Bastion NSG rules may change')
        seen.add(identity)
    if seen!=allowed:raise ValueError('Incomplete forward repair what-if')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','validate','what-if','deploy','state','verify'));args=parser.parse_args()
    if az('account','show','--query','id')!=p.SUB or az('ad','signed-in-user','show','--query','id')!=p.OWNER:raise ValueError('Approved owner/subscription required')
    original=read(p.ROOT,'plan');read(p.ROOT,'deploy-intent');p.verify_boundaries(original)
    old=p.request('GET',p.DEPLOYMENT+API)
    if old['properties']['provisioningState']!='Failed':raise ValueError('Only a known failed additive deployment is eligible')
    if args.stage=='prepare':
        if ROOT.exists() or p.request('GET',DEPLOYMENT+API,missing=True) is not None:raise ValueError('Preserve prior forward repair')
        errors=az('deployment','operation','group','list','-g',RG,'-n',p.DEPLOYMENT_NAME,'--query',"[?properties.provisioningState=='Failed'].{id:properties.targetResource.id,code:properties.statusMessage.error.code}")
        expected={p.VNET+'/subnets/AzureBastionSubnet':'NetworkSecurityGroupNotCompliantForAzureBastionSubnet',p.rid('Microsoft.Network/networkSecurityGroups','ibkrv2stg-admin-nsg'):'SecurityRuleInvalidAccessType'}
        if {r['id']:r['code'] for r in errors}!=expected:raise ValueError('Failed deployment contains different causes')
        prior=next(r for r in original['template']['resources'] if r['name']=='ibkrv2stg-bastion-nsg')
        actual=p.request('GET',p.rid(prior['type'],prior['name'])+'?api-version='+p.NET_API)
        if not rules_match(actual['properties']['securityRules'],prior['properties']['securityRules']) or actual['tags'].get('purpose')!=p.TAGS['purpose']:raise ValueError('Only this task-created NSG may be corrected')
        value=correction(original['template']);ROOT.mkdir()
        save(ROOT,'plan',{'template':value,'resource_ids':sorted(p.expected_ids(value)),'retained_support':support_baseline(),'failed_deployment':p.DEPLOYMENT,'failed_plan_sha256':hashlib.sha256((p.ROOT/'plan.json').read_bytes()).hexdigest(),'errors':errors,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'public_service_access_unchanged':True,'changes':'Remove unsupported Allow AzurePlatformDNS rule; Bastion required SSH/RDP egress uses VirtualNetwork; target VM still accepts only Bastion SSH. Reference existing task-created NAT/PIPs unchanged, preserving their DDoS and SKU defaults.','authorization':original['authorization']})
        (ROOT/'template.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print('Prepared exact two-rule forward repair; failed evidence preserved');return
    plan=read(ROOT,'plan')
    if plan['controller_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or plan['template']!=correction(original['template']):raise ValueError('Frozen forward repair drift')
    if support_baseline()!=plan['retained_support']:raise ValueError('Retained NAT/public-IP security or allocation drift')
    if args.stage=='validate':
        result=az('deployment','group','validate','-g',RG,'-n',NAME,'--template-file',str(ROOT/'template.json'))
        if result.get('error'):raise ValueError('Forward template validation failed')
        if not (ROOT/'validated.json').exists():save(ROOT,'validated',{'status':'passed'})
        print('Forward repair validation passed');return
    if args.stage=='what-if':
        read(ROOT,'validated');result=az('deployment','group','what-if','-g',RG,'-n',NAME,'--template-file',str(ROOT/'template.json'),'--no-pretty-print')
        if result.get('status')!='Succeeded':raise ValueError('Forward repair what-if failed')
        check_changes(result['changes'],plan['resource_ids'])
        if not (ROOT/'what-if-qualified.json').exists():save(ROOT,'what-if-qualified',{'status':'passed','changes':[{'resourceId':c['resourceId'],'changeType':c['changeType']} for c in result['changes'] if c['changeType']!='Ignore']})
        print('Forward repair changes are limited to this task-created access path');return
    if args.stage=='deploy':
        read(ROOT,'what-if-qualified')
        if (ROOT/'deploy-intent.json').exists():raise ValueError('Inspect forward operation; never repeat blindly')
        save(ROOT,'deploy-intent',{'template_sha256':hashlib.sha256((ROOT/'template.json').read_bytes()).hexdigest(),'deployment':DEPLOYMENT})
        response=p.request('PUT',DEPLOYMENT+API,data={'properties':{'mode':'Incremental','template':plan['template']}})
        save(ROOT,'deploy-ack',{'id':response.get('id'),'state':response['properties'].get('provisioningState')});print('Submitted qualified forward access-path deployment; existing public access unchanged');return
    current=p.request('GET',DEPLOYMENT+API,missing=True);state=current['properties']['provisioningState'] if current else 'NotDispatched'
    print(json.dumps({'state':state,'public_service_access_unchanged':True}),flush=True)
    if args.stage=='state' or state!='Succeeded':return
    verify_new_network(plan['template'])
    vm=p.request('GET',p.rid('Microsoft.Compute/virtualMachines',p.VM)+'?api-version='+p.COMPUTE_API)
    nic=p.request('GET',p.rid('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic')+'?api-version='+p.NET_API)
    bastion=p.request('GET',p.rid('Microsoft.Network/bastionHosts',p.BASTION)+'?api-version='+p.NET_API)
    ext=p.request('GET',vm['id']+'/extensions/AADSSHLoginForLinux?api-version='+p.COMPUTE_API)
    if any(x['properties']['provisioningState']!='Succeeded' for x in (vm,nic,bastion,ext)):raise ValueError('Access components not Ready')
    if vm['properties']['hardwareProfile']['vmSize']!=p.SIZE or not vm['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication'] or vm['identity']['type']!='SystemAssigned' or any(x['properties'].get('publicIPAddress') for x in nic['properties']['ipConfigurations']):raise ValueError('Private VM security differs')
    if bastion['sku']['name']!='Standard' or not bastion['properties']['enableTunneling'] or bastion['properties'].get('enableIpConnect') or bastion['properties'].get('enableShareableLink'):raise ValueError('Bastion configuration differs')
    roles=az('role','assignment','list','--scope',vm['id'],'--query',"[?roleDefinitionName=='Virtual Machine Administrator Login'].{principalId:principalId,scope:scope}")
    if roles!=[{'principalId':p.OWNER,'scope':vm['id']}]:raise ValueError('Only the owner may receive new VM login')
    record={'status':'infrastructure_ready','vm':vm['id'],'bastion':bastion['id'],'vm_public_ip':False,'owner_only_vm_login':True,'entra_extension_ready':True,'existing_network_and_public_access_unchanged':True,'bastion_login_tested':False,'private_build_tested':False,'public_access_restriction_allowed':False,'forward_repair_plan_sha256':hashlib.sha256((ROOT/'plan.json').read_bytes()).hexdigest()}
    if not (ROOT/'qualified.json').exists():save(ROOT,'qualified',record)
    if not (p.ROOT/'infrastructure-qualified.json').exists():save(p.ROOT,'infrastructure-qualified',record)
    print(json.dumps(record))

if __name__=='__main__':main()
