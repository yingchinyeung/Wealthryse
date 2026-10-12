"""Read-back acceptance of the expected new extension child; no resource writes."""
import copy,hashlib,json
from pathlib import Path
import repair_viewer_private_admin_extension_20261008 as repair
from qualify_viewer_database_recovery_20261008 import read,save
from audit_azure_runtime import az

p=repair.p

def owner_role_matches(roles,vm):
    # Azure normalizes resourceGroups to resourcegroups in role-assignment IDs.
    return len(roles)==1 and roles[0].get('principalId')==p.OWNER and roles[0].get('scope','').lower()==vm.lower()

def check_retained(saved,current,wanted):
    actual=copy.deepcopy(current)
    vm=p.rid('Microsoft.Compute/virtualMachines',p.VM)
    children=actual[vm].pop('resources',[])
    if len(children)!=1 or children[0]['id'].lower()!=repair.EXT.lower():raise ValueError('Only the approved extension child may be added')
    child=children[0]
    if child['type'].lower()!='microsoft.compute/virtualmachines/extensions' or any(child['properties'].get(k)!=v for k,v in wanted.items()) or child['properties'].get('enableAutomaticUpgrade') is True:raise ValueError('Extension child properties differ')
    if actual!=saved:raise ValueError('Retained VM, Bastion, NIC or subnet security/configuration differs')

def verify_retained(plan,prior):
    wanted=next(r['properties'] for r in plan['template']['resources'] if r['type']=='Microsoft.Compute/virtualMachines/extensions')
    check_retained(plan['baseline'],repair.baseline(prior['template']),wanted)
    ext=p.request('GET',repair.EXT+'?api-version='+p.COMPUTE_API)
    if ext['properties']['provisioningState']!='Succeeded':raise ValueError('Extension must be Ready')

def main():
    if az('account','show','--query','id')!=p.SUB or az('ad','signed-in-user','show','--query','id')!=p.OWNER:raise ValueError('Exact approved owner/subscription required')
    plan=read(repair.ROOT,'plan');prior=read(repair.previous.ROOT,'plan');original=read(p.ROOT,'plan')
    read(repair.ROOT,'deploy-intent');read(repair.ROOT,'what-if-qualified')
    if plan['controller_sha256']!=hashlib.sha256(Path(repair.__file__).read_bytes()).hexdigest() or plan['template']!=repair.correction(prior['template']) or plan['prior_plan_sha256']!=hashlib.sha256((repair.previous.ROOT/'plan.json').read_bytes()).hexdigest():raise ValueError('Preserved qualified plan/controller drift')
    repair.verify_boundaries(original);repair.previous.verify_new_network(prior['template'])
    if repair.previous.support_baseline()!=prior['retained_support']:raise ValueError('Support resource drift')
    current=p.request('GET',repair.DEPLOYMENT+p.ARM_API)
    state=current['properties']['provisioningState'];print(json.dumps({'deployment_state':state}),flush=True)
    if state!='Succeeded':return
    verify_retained(plan,prior)
    vm=p.rid('Microsoft.Compute/virtualMachines',p.VM)
    machine=p.request('GET',vm+'?api-version='+p.COMPUTE_API)
    nic=p.request('GET',p.rid('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic')+'?api-version='+p.NET_API)
    bastion=p.request('GET',p.rid('Microsoft.Network/bastionHosts',p.BASTION)+'?api-version='+p.NET_API)
    if machine['properties']['hardwareProfile']['vmSize']!=p.SIZE or machine['identity']['type']!='SystemAssigned' or not machine['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication'] or any(x['properties'].get('publicIPAddress') for x in nic['properties']['ipConfigurations']):raise ValueError('Private VM security differs')
    if bastion['sku']['name']!='Standard' or not bastion['properties']['enableTunneling'] or bastion['properties'].get('enableIpConnect') or bastion['properties'].get('enableShareableLink'):raise ValueError('Restricted Bastion configuration differs')
    roles=az('role','assignment','list','--scope',vm,'--query',"[?roleDefinitionName=='Virtual Machine Administrator Login'].{principalId:principalId,scope:scope}")
    if not owner_role_matches(roles,vm):raise ValueError('Only exact owner VM login permitted')
    record={'status':'infrastructure_ready','vm':vm,'bastion':bastion['id'],'vm_public_ip':False,'owner_only_vm_login':True,'entra_extension_ready':True,'existing_network_and_public_access_unchanged':True,'bastion_login_tested':False,'private_build_tested':False,'public_access_restriction_allowed':False,'forward_repair_plan_sha256':plan['prior_plan_sha256'],'extension_repair_plan_sha256':hashlib.sha256((repair.ROOT/'plan.json').read_bytes()).hexdigest(),'readback_verifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if not (repair.ROOT/'qualified.json').exists():save(repair.ROOT,'qualified',record)
    if not (p.ROOT/'infrastructure-qualified.json').exists():save(p.ROOT,'infrastructure-qualified',record)
    print('Private access infrastructure passed; real login/build still required')

if __name__=='__main__':main()
