"""Registry-scoped push/pull for the previously approved private build VM."""
import json,sys,uuid
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid,SUB
from qualify_upgrade_private_tls_20261009 import vm_boundary
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9/private-build-role'
REG=rid('Microsoft.ContainerRegistry/registries','ibkrv2stgauhecwc4itaoo')
REGISTRY_NAME='ibkrv2stgauhecwc4itaoo'
PRINCIPAL='f97c7df8-85ab-4a07-91c7-4335e862679f'
ROLE='/subscriptions/'+SUB+'/providers/Microsoft.Authorization/roleDefinitions/8311e382-0749-4cb8-b61a-304f252e45ec'
ASSIGNMENT=REG+'/providers/Microsoft.Authorization/roleAssignments/'+str(uuid.uuid5(uuid.NAMESPACE_URL,REG.lower()+'|'+PRINCIPAL+'|'+ROLE.lower()))
API='?api-version=2022-04-01'

def configure_legacy():
    global REG,REGISTRY_NAME,ROOT,ASSIGNMENT
    REGISTRY_NAME='ibkrv115rcc62xxqblay'
    REG=rid('Microsoft.ContainerRegistry/registries',REGISTRY_NAME)
    ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/legacy-private-build-role'
    ASSIGNMENT=REG+'/providers/Microsoft.Authorization/roleAssignments/'+str(uuid.uuid5(uuid.NAMESPACE_URL,REG.lower()+'|'+PRINCIPAL+'|'+ROLE.lower()))

def save(name,value):
    receipt=retain(value,'upgrade-private-build-role-'+name)
    with (ROOT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':receipt},f,indent=2)

def load(name):
    value=json.loads((ROOT/(name+'.json')).read_bytes());remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected evidence changed')
    return value

def boundary():
    vm=vm_boundary();reg=request('GET',REG+'?api-version=2023-07-01')
    role=request('GET',ROLE+API)['properties']
    permissions=[{'actions':['Microsoft.ContainerRegistry/registries/pull/read','Microsoft.ContainerRegistry/registries/push/write'],'notActions':[],'dataActions':[],'notDataActions':[]}]
    if role['roleName']!='AcrPush' or role['permissions']!=permissions:raise ValueError('Role exceeds approved push/pull scope')
    mode=az('acr','show','--name',REGISTRY_NAME,'--query','roleAssignmentMode')
    if mode!='LegacyRegistryPermissions' or reg['properties']['loginServer']!=REGISTRY_NAME+'.azurecr.io':raise ValueError('Registry mode differs')
    return {'vm':vm,'registry':reg['id'],'permissions':permissions,'role_assignment_mode':mode,'registry_network':{k:reg['properties'].get(k) for k in ('publicNetworkAccess','networkRuleSet')}}

def check_assignment(value):
    p=value['properties']
    if p['principalId']!=PRINCIPAL or p['principalType']!='ServicePrincipal' or p['roleDefinitionId'].lower()!=ROLE.lower() or p['scope'].lower()!=REG.lower() or p.get('condition'):raise ValueError('Assignment differs')

def main(stage):
    current=boundary();existing=request('GET',ASSIGNMENT+API,missing=True)
    if stage=='plan':
        if ROOT.exists() or existing is not None:raise ValueError('Preserve and reconcile existing role plan')
        roles=request('GET',REG+'/providers/Microsoft.Authorization/roleAssignments'+API+'&$filter=principalId%20eq%20%27'+PRINCIPAL+'%27')
        if roles.get('nextLink') or roles.get('value'):raise ValueError('Review existing identity permissions first')
        ROOT.mkdir(parents=True)
        save('plan',{'boundary':current,'assignment':ASSIGNMENT,'body':{'properties':{'roleDefinitionId':ROLE,'principalId':PRINCIPAL,'principalType':'ServicePrincipal','description':'Approved WealthRyse private administration/build path: registry-scoped image push/pull only'}},'authorization':'Owner approved private administration/build path and full upgrade','rollback':{'method':'DELETE','resource':ASSIGNMENT,'condition':'Only this exact newly created assignment, after confirming no ongoing private build'},'production_dns_changed':False,'public_access_changed':False})
        print('Protected registry-only push/pull plan; no role granted yet');return
    plan=load('plan')
    if current!=plan['boundary']:raise ValueError('VM, registry or permissions drift')
    if stage=='apply':
        if existing is not None or (ROOT/'apply-intent.json').exists():raise ValueError('Reconcile existing grant; never retry blindly')
        save('apply-intent',{'assignment':ASSIGNMENT,'plan_receipt':plan['protected_receipt']})
        result=request('PUT',ASSIGNMENT+API,data=plan['body']);check_assignment(result)
        save('apply-ack',{'id':result['id']});print('Registry-scoped AcrPush granted to private VM identity');return
    if stage!='verify':raise ValueError('Unknown stage')
    if existing is None:raise ValueError('Assignment not found')
    check_assignment(existing)
    save('verified',{'passed':True,'assignment':ASSIGNMENT,'registry_only':True,'production_dns_changed':False,'public_access_changed':False})
    print('Private build identity push/pull verified; production network unchanged')

if __name__=='__main__':
    if sys.argv[2:]==['legacy']:configure_legacy()
    elif sys.argv[2:]:raise ValueError('Unknown registry scope')
    main(sys.argv[1])
