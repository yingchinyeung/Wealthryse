"""New-VM sign-in extension repair only; retain failed plans and public paths."""
import argparse,copy,hashlib,json
from pathlib import Path
import deploy_viewer_private_admin_20261008 as p
import repair_viewer_private_admin_20261008 as previous
from qualify_viewer_database_recovery_20261008 import read,save
from audit_azure_runtime import az,RG

ROOT=p.ROOT/'extension-repair-v5'
NAME='viewer-private-admin-signin-20261008-v5'
DEPLOYMENT=p.BASE+'Microsoft.Resources/deployments/'+NAME
EXT=p.rid('Microsoft.Compute/virtualMachines/extensions',p.VM+'/AADSSHLoginForLinux')

def correction(template):
    value=copy.deepcopy(template)
    value['resources']=[r for r in value['resources'] if r['type'] in ('Microsoft.Compute/virtualMachines/extensions','Microsoft.Authorization/roleAssignments')]
    if len(value['resources'])!=2:raise ValueError('Exact extension and owner login assignment required')
    for r in value['resources']:
        if r['type']=='Microsoft.Compute/virtualMachines/extensions':
            if r['name']!=p.VM+'/AADSSHLoginForLinux' or r['properties'].pop('enableAutomaticUpgrade',None) is not True:raise ValueError('Exact unsupported setting required')
        else:
            if r['scope']!=p.rid('Microsoft.Compute/virtualMachines',p.VM) or r['properties']['principalId']!=p.OWNER or not r['properties']['roleDefinitionId'].endswith('/'+p.ROLE):raise ValueError('Exact owner-only new-VM role required')
        r['dependsOn']=[x for x in r['dependsOn'] if x==EXT]
    return value

def stable(value):
    if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('etag','provisioningState','resourceGuid')}
    if isinstance(value,list):return [stable(v) for v in value]
    return value

def without_etag(value):
    if isinstance(value,dict):return {k:without_etag(v) for k,v in value.items() if k!='etag'}
    if isinstance(value,list):return [without_etag(v) for v in value]
    return value

def verify_boundaries(plan):
    current=p.request('GET',p.VNET+'?api-version='+p.NET_API)
    # A subnet addition rotates delegation etags throughout the VNet. Preserve
    # serviceName, actions, IDs, readiness, routes, ACLs and every semantic field.
    if without_etag(p.existing_network_scope(current))!=without_etag(plan['existing_network_scope']):raise ValueError('Existing network semantics drift')
    for original in plan['public_boundaries']:
        actual=p.request('GET',original['id']+'?api-version='+original['api'])
        if {key:actual['properties'].get(key) for key in original['properties']}!=original['properties']:raise ValueError('Existing public service boundary drift')

def baseline(template):
    result={}
    for r in template['resources']:
        if r['type'] in ('Microsoft.Compute/virtualMachines/extensions','Microsoft.Authorization/roleAssignments'):continue
        identity=p.rid(r['type'],r['name']);api=p.COMPUTE_API if r['type'].startswith('Microsoft.Compute/') else p.NET_API
        value=p.request('GET',identity+'?api-version='+api)
        if value['properties']['provisioningState']!='Succeeded':raise ValueError('Existing new access components must be Ready')
        result[identity]=stable(value)
    return result

def check_changes(changes,ids):
    wanted={x.lower() for x in ids};seen=set()
    for c in changes:
        identity=c['resourceId'].lower()
        if c['changeType']=='Ignore' and identity.startswith(p.BASE.lower()) and c.get('delta') in (None,[]):continue
        if identity not in wanted or c['changeType']!='Create':raise ValueError('Only absent new-VM extension/owner role may be created')
        seen.add(identity)
    if seen!=wanted:raise ValueError('Incomplete extension/role what-if')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','validate','what-if','deploy','state','verify'));args=parser.parse_args()
    if az('account','show','--query','id')!=p.SUB or az('ad','signed-in-user','show','--query','id')!=p.OWNER:raise ValueError('Approved owner/subscription required')
    original=read(p.ROOT,'plan');prior=read(previous.ROOT,'plan');verify_boundaries(original)
    if previous.support_baseline()!=prior['retained_support']:raise ValueError('Retained support resources drift')
    previous.verify_new_network(prior['template'])
    value=correction(prior['template']);ids=p.expected_ids(value)
    if args.stage=='prepare':
        if ROOT.exists() or p.request('GET',DEPLOYMENT+p.ARM_API,missing=True) is not None:raise ValueError('Preserve earlier repair')
        old=p.request('GET',previous.DEPLOYMENT+p.ARM_API)
        errors=az('deployment','operation','group','list','-g',RG,'-n',previous.NAME,'--query',"[?properties.provisioningState=='Failed'].{id:properties.targetResource.id,code:properties.statusMessage.error.code}")
        if old['properties']['provisioningState']!='Failed' or errors!=[{'id':EXT,'code':'OperationNotAllowed'}]:raise ValueError('Known extension-only provisioning failure required')
        for identity in ids:
            api='2022-04-01' if '/roleAssignments/' in identity else p.COMPUTE_API
            if p.request('GET',identity+'?api-version='+api,missing=True) is not None:raise ValueError('Do not overwrite an existing extension/role')
        preserved=baseline(prior['template']);ROOT.mkdir()
        save(ROOT,'plan',{'template':value,'resource_ids':sorted(ids),'baseline':preserved,'prior_plan_sha256':hashlib.sha256((previous.ROOT/'plan.json').read_bytes()).hexdigest(),'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'errors':errors,'authorization':original['authorization'],'public_access_restriction_allowed':False})
        (ROOT/'template.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print('Prepared extension-only repair; existing access resources preserved');return
    plan=read(ROOT,'plan')
    if plan['template']!=value or plan['controller_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or plan['baseline']!=baseline(prior['template']):raise ValueError('Frozen extension repair or retained resources drift')
    if args.stage=='validate':
        result=az('deployment','group','validate','-g',RG,'-n',NAME,'--template-file',str(ROOT/'template.json'))
        if result.get('error'):raise ValueError('Extension repair validation failed')
        if not (ROOT/'validated.json').exists():save(ROOT,'validated',{'status':'passed'})
        print('Extension repair validation passed');return
    if args.stage=='what-if':
        read(ROOT,'validated');result=az('deployment','group','what-if','-g',RG,'-n',NAME,'--template-file',str(ROOT/'template.json'),'--no-pretty-print')
        if result.get('status')!='Succeeded':raise ValueError('What-if failed')
        check_changes(result['changes'],ids)
        if not (ROOT/'what-if-qualified.json').exists():save(ROOT,'what-if-qualified',{'status':'passed','changes':[{'resourceId':c['resourceId'],'changeType':c['changeType']} for c in result['changes'] if c['changeType']!='Ignore']})
        print('What-if confirms only new-VM extension and owner-scoped login role');return
    if args.stage=='deploy':
        read(ROOT,'what-if-qualified')
        if (ROOT/'deploy-intent.json').exists():raise ValueError('Inspect rather than repeat deployment')
        save(ROOT,'deploy-intent',{'deployment':DEPLOYMENT,'template_sha256':hashlib.sha256((ROOT/'template.json').read_bytes()).hexdigest()})
        response=p.request('PUT',DEPLOYMENT+p.ARM_API,data={'properties':{'mode':'Incremental','template':value}})
        save(ROOT,'deploy-ack',{'state':response['properties'].get('provisioningState')});print('Submitted extension-only repair; public access unchanged');return
    current=p.request('GET',DEPLOYMENT+p.ARM_API,missing=True);state=current['properties']['provisioningState'] if current else 'NotDispatched'
    print(json.dumps({'state':state,'public_access_unchanged':True}),flush=True)
    if args.stage=='state' or state!='Succeeded':return
    ext=p.request('GET',EXT+'?api-version='+p.COMPUTE_API)
    if ext['properties']['provisioningState']!='Succeeded' or ext['properties'].get('enableAutomaticUpgrade') is True:raise ValueError('Sign-in extension not ready')
    vm=p.rid('Microsoft.Compute/virtualMachines',p.VM)
    machine=p.request('GET',vm+'?api-version='+p.COMPUTE_API)
    nic=p.request('GET',p.rid('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic')+'?api-version='+p.NET_API)
    bastion=p.request('GET',p.rid('Microsoft.Network/bastionHosts',p.BASTION)+'?api-version='+p.NET_API)
    if machine['properties']['hardwareProfile']['vmSize']!=p.SIZE or machine['identity']['type']!='SystemAssigned' or not machine['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication'] or any(x['properties'].get('publicIPAddress') for x in nic['properties']['ipConfigurations']):raise ValueError('Private VM security differs')
    if bastion['sku']['name']!='Standard' or not bastion['properties']['enableTunneling'] or bastion['properties'].get('enableIpConnect') or bastion['properties'].get('enableShareableLink'):raise ValueError('Restricted Bastion configuration differs')
    roles=az('role','assignment','list','--scope',vm,'--query',"[?roleDefinitionName=='Virtual Machine Administrator Login'].{principalId:principalId,scope:scope}")
    if roles!=[{'principalId':p.OWNER,'scope':vm}]:raise ValueError('Only owner new-VM login role permitted')
    record={'status':'infrastructure_ready','vm':vm,'bastion':p.rid('Microsoft.Network/bastionHosts',p.BASTION),'vm_public_ip':False,'owner_only_vm_login':True,'entra_extension_ready':True,'existing_network_and_public_access_unchanged':True,'bastion_login_tested':False,'private_build_tested':False,'public_access_restriction_allowed':False,'forward_repair_plan_sha256':plan['prior_plan_sha256'],'extension_repair_plan_sha256':hashlib.sha256((ROOT/'plan.json').read_bytes()).hexdigest()}
    if not (ROOT/'qualified.json').exists():save(ROOT,'qualified',record)
    if not (p.ROOT/'infrastructure-qualified.json').exists():save(p.ROOT,'infrastructure-qualified',record)
    print('Private administration infrastructure qualified; real login/build still required')

if __name__=='__main__':main()
