"""Approved additive private administration path; never restrict production access.

Only a new VM, Bastion and their dedicated subnets/support resources are eligible.
Existing subnets and application boundaries are read back before every mutation.
No database credentials, SSH private key or passwords are archived or installed.
"""
import argparse,copy,hashlib,ipaddress,json,uuid
import requests
from azure.identity import AzureCliCredential
from datetime import datetime,timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from audit_azure_runtime import az,RG
from deploy_metadata_monitor import BASE,SUB
from qualify_viewer_database_recovery_20261008 import save,read
from preflight_viewer_maintenance_20261008 import FOLDER

ROOT=FOLDER/'private-administration-v2'
NETWORK='ibkrv2stg-vnet'
VNET=BASE+'Microsoft.Network/virtualNetworks/'+NETWORK
DEPLOYMENT_NAME='viewer-private-admin-20261008-v2'
DEPLOYMENT=BASE+'Microsoft.Resources/deployments/'+DEPLOYMENT_NAME
OWNER='8617fd1c-2280-40f2-92dd-45686e9d686c'
ROLE='1c0163c0-47e6-4577-8991-ea5c82e286e4'
VM='ibkrv2stg-private-admin'
BASTION='ibkrv2stg-admin-bastion'
SIZE='Standard_D2s_v4'
BASTION_PREFIX='10.50.8.0/26'
ADMIN_PREFIX='10.50.9.0/27'
NET_API='2024-05-01'
COMPUTE_API='2024-11-01'
ARM_API='?api-version=2022-09-01'
TAGS={'application':'ibkr-portfolio-platform','purpose':'private-administration-build','owner':'portfolio-platform'}
_TOKEN=None

def request(method,path,*,data=None,missing=False):
    # One credential acquisition per bounded controller process, never archived.
    # Concurrent read checks must not flood the local CLI with token subprocesses.
    global _TOKEN
    if _TOKEN is None:_TOKEN=AzureCliCredential(process_timeout=60).get_token('https://management.azure.com/.default').token
    response=requests.request(method,'https://management.azure.com'+path,headers={'Authorization':'Bearer '+_TOKEN},json=data,timeout=60)
    if missing and response.status_code==404:return None
    if not response.ok:raise RuntimeError('Azure '+method+' failed HTTP '+str(response.status_code))
    return response.json() if response.content else {}

def rid(kind,name):
    provider,*types=kind.split('/')
    names=name.split('/')
    if len(types)!=len(names):raise ValueError('Resource type/name depth differs')
    return BASE+provider+'/'+('/'.join(part for pair in zip(types,names) for part in pair))

def existing_network_scope(vnet):
    # Runtime NIC/managed database addresses may change; permissions/routes may not.
    keys=('addressPrefix','addressPrefixes','delegations','networkSecurityGroup','routeTable','natGateway','serviceEndpoints','privateEndpointNetworkPolicies','privateLinkServiceNetworkPolicies','defaultOutboundAccess')
    return {'id':vnet['id'],'location':vnet['location'],'addressSpace':vnet['properties']['addressSpace'],'dns':vnet['properties'].get('dhcpOptions'),
        'subnets':{s['name']:{k:s['properties'].get(k) for k in keys} for s in vnet['properties']['subnets'] if s['name'] not in ('AzureBastionSubnet','private-administration')}}

def check_prefixes(vnet):
    containing=[ipaddress.ip_network(x) for x in vnet['properties']['addressSpace']['addressPrefixes']]
    proposed=[ipaddress.ip_network(BASTION_PREFIX),ipaddress.ip_network(ADMIN_PREFIX)]
    existing=[ipaddress.ip_network(x) for s in vnet['properties']['subnets'] for x in ([s['properties']['addressPrefix']] if s['properties'].get('addressPrefix') else s['properties'].get('addressPrefixes',[]))]
    if any(not any(n.subnet_of(parent) for parent in containing) for n in proposed) or proposed[0].overlaps(proposed[1]) or any(n.overlaps(old) for n in proposed for old in existing):
        raise ValueError('Dedicated subnet prefixes are outside scope or overlap')
    if any(s['name'] in ('AzureBastionSubnet','private-administration') for s in vnet['properties']['subnets']):raise ValueError('Preserve existing management subnet')

def rule(name,priority,direction,source,destination,ports,access='Allow',protocol='Tcp'):
    fields={'priority':priority,'direction':direction,'access':access,'protocol':protocol,'sourcePortRange':'*','sourceAddressPrefix':source,'destinationAddressPrefix':destination}
    fields['destinationPortRange' if isinstance(ports,str) else 'destinationPortRanges']=ports
    return {'name':name,'properties':fields}

def template(public_key,image_version):
    resources=[]
    def add(kind,name,properties,depends=(),api=NET_API,**extra):
        value={'type':kind,'apiVersion':api,'name':name,'properties':properties,'dependsOn':list(depends),**extra}
        if kind not in ('Microsoft.Network/virtualNetworks/subnets','Microsoft.Authorization/roleAssignments'):value.update(location='eastasia',tags=TAGS)
        resources.append(value);return rid(kind,name)
    bp=add('Microsoft.Network/publicIPAddresses','ibkrv2stg-bastion-pip',{'publicIPAllocationMethod':'Static','publicIPAddressVersion':'IPv4'},sku={'name':'Standard'})
    np=add('Microsoft.Network/publicIPAddresses','ibkrv2stg-admin-egress-pip',{'publicIPAllocationMethod':'Static','publicIPAddressVersion':'IPv4'},sku={'name':'Standard'})
    nat=add('Microsoft.Network/natGateways','ibkrv2stg-admin-egress',{'idleTimeoutInMinutes':4,'publicIpAddresses':[{'id':np}]},(np,),sku={'name':'Standard'})
    bastion_rules=[rule('AllowHttpsInbound',100,'Inbound','Internet','*','443'),rule('AllowGatewayManagerInbound',110,'Inbound','GatewayManager','*','443'),
        rule('AllowBastionCommunicationInbound',120,'Inbound','VirtualNetwork','VirtualNetwork',['8080','5701'],protocol='*'),rule('AllowAzureLoadBalancerInbound',130,'Inbound','AzureLoadBalancer','*','443'),
        rule('DenyOtherInbound',4000,'Inbound','*','*','*',access='Deny',protocol='*'),
        rule('AllowSshRdpOutbound',100,'Outbound','*',ADMIN_PREFIX,['22','3389'],protocol='*'),rule('AllowAzureCloudOutbound',110,'Outbound','*','AzureCloud','443'),
        rule('AllowBastionCommunicationOutbound',120,'Outbound','VirtualNetwork','VirtualNetwork',['8080','5701'],protocol='*'),rule('AllowHttpOutbound',130,'Outbound','*','Internet','80',protocol='*'),
        rule('DenyOtherOutbound',4000,'Outbound','*','*','*',access='Deny',protocol='*')]
    bn=add('Microsoft.Network/networkSecurityGroups','ibkrv2stg-bastion-nsg',{'securityRules':bastion_rules})
    admin_rules=[rule('AllowBastionSshOnly',100,'Inbound',BASTION_PREFIX,'*','22'),rule('DenyOtherInbound',200,'Inbound','*','*','*',access='Deny',protocol='*'),
        rule('AllowPrivatePostgres',100,'Outbound','*','10.50.4.0/28','5432'),rule('AllowPlatformDns',110,'Outbound','*','AzurePlatformDNS','53',protocol='*'),
        rule('AllowAzureAgent',120,'Outbound','*','168.63.129.16',['80','32526']),rule('AllowInstanceMetadata',130,'Outbound','*','169.254.169.254','80'),
        rule('DenyOtherPrivateNetwork',200,'Outbound','*','VirtualNetwork','*',access='Deny',protocol='*'),rule('AllowHttpsUpdatesBuildAndIdentity',300,'Outbound','*','Internet',['443','80']),
        rule('DenyOtherOutbound',4000,'Outbound','*','*','*',access='Deny',protocol='*')]
    an=add('Microsoft.Network/networkSecurityGroups','ibkrv2stg-admin-nsg',{'securityRules':admin_rules})
    bs=add('Microsoft.Network/virtualNetworks/subnets',NETWORK+'/AzureBastionSubnet',{'addressPrefix':BASTION_PREFIX,'networkSecurityGroup':{'id':bn}},(bn,))
    ads=add('Microsoft.Network/virtualNetworks/subnets',NETWORK+'/private-administration',{'addressPrefix':ADMIN_PREFIX,'defaultOutboundAccess':False,'networkSecurityGroup':{'id':an},'natGateway':{'id':nat}},(bs,an,nat))
    nic=add('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic',{'enableIPForwarding':False,'ipConfigurations':[{'name':'private','properties':{'privateIPAllocationMethod':'Dynamic','subnet':{'id':ads},'primary':True}}]},(ads,))
    vm=add('Microsoft.Compute/virtualMachines',VM,{'hardwareProfile':{'vmSize':SIZE},'storageProfile':{'imageReference':{'publisher':'Canonical','offer':'ubuntu-24_04-lts','sku':'server','version':image_version},'osDisk':{'name':VM+'-os','createOption':'FromImage','diskSizeGB':64,'managedDisk':{'storageAccountType':'Premium_LRS'},'deleteOption':'Detach'}},
        'osProfile':{'computerName':'ibkr-private-admin','adminUsername':'platformadmin','linuxConfiguration':{'disablePasswordAuthentication':True,'provisionVMAgent':True,'ssh':{'publicKeys':[{'path':'/home/platformadmin/.ssh/authorized_keys','keyData':public_key}]},'patchSettings':{'patchMode':'AutomaticByPlatform','assessmentMode':'AutomaticByPlatform'}}},
        'securityProfile':{'securityType':'TrustedLaunch','uefiSettings':{'secureBootEnabled':True,'vTpmEnabled':True}},'networkProfile':{'networkInterfaces':[{'id':nic,'properties':{'primary':True,'deleteOption':'Detach'}}]},'diagnosticsProfile':{'bootDiagnostics':{'enabled':True}}},(nic,),api=COMPUTE_API,identity={'type':'SystemAssigned'})
    extension=add('Microsoft.Compute/virtualMachines/extensions',VM+'/AADSSHLoginForLinux',{'publisher':'Microsoft.Azure.ActiveDirectory','type':'AADSSHLoginForLinux','typeHandlerVersion':'1.0','autoUpgradeMinorVersion':True,'enableAutomaticUpgrade':True},(vm,),api=COMPUTE_API)
    role_name=str(uuid.uuid5(uuid.NAMESPACE_URL,vm+OWNER+ROLE))
    # Extension readiness first; this role applies only to the new VM, never RG/subscription.
    resources.append({'type':'Microsoft.Authorization/roleAssignments','apiVersion':'2022-04-01','name':role_name,'scope':vm,'dependsOn':[vm,extension],'properties':{'principalId':OWNER,'principalType':'User','roleDefinitionId':'/subscriptions/'+SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+ROLE}})
    add('Microsoft.Network/bastionHosts',BASTION,{'scaleUnits':2,'enableTunneling':True,'enableIpConnect':False,'enableShareableLink':False,'ipConfigurations':[{'name':'private-admin','properties':{'subnet':{'id':bs},'publicIPAddress':{'id':bp}}}]},(bs,bp),sku={'name':'Standard'})
    return {'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':resources}

def expected_ids(value):
    return {r['scope']+'/providers/'+r['type']+'/'+r['name'] if r.get('scope') else rid(r['type'],r['name']) for r in value['resources']}

def safe_changes(changes,allowed):
    wanted={x.lower() for x in allowed}
    # Incremental what-if reports unrelated existing resources as Ignore, with
    # no delta. That is not an edit. Keep rejecting every real out-of-scope edit.
    ignored=[c for c in changes if c['changeType']=='Ignore']
    creates=[c for c in changes if c['changeType']!='Ignore']
    if any(not c['resourceId'].lower().startswith(BASE.lower()) or c.get('delta') not in (None,[]) for c in ignored):
        raise ValueError('Unexpected ignored-resource scope/delta')
    if not creates or any(c['resourceId'].lower() not in wanted or c['changeType']!='Create' for c in creates):
        raise ValueError('What-if includes existing resource edits, deletion or unknown scope')
    if {c['resourceId'].lower() for c in changes if c['changeType']=='Create'}!=wanted:
        raise ValueError('What-if does not cover every new approved resource')

def verify_boundaries(plan):
    current=request('GET',VNET+'?api-version='+NET_API)
    if existing_network_scope(current)!=plan['existing_network_scope']:raise ValueError('Existing subnet/address/route boundary drift')
    # No private endpoint, public ACL or credential edits belong to this controller.
    for original in plan['public_boundaries']:
        actual=request('GET',original['id']+'?api-version='+original['api'])
        if {key:actual['properties'].get(key) for key in original['properties']}!=original['properties']:raise ValueError('Existing public service boundary drift')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','validate','what-if','deploy','state','verify'));args=parser.parse_args()
    if az('account','show','--query','id')!=SUB or az('ad','signed-in-user','show','--query','id')!=OWNER:raise ValueError('Approved Azure owner/subscription required')
    if args.stage=='prepare':
        if ROOT.exists():raise ValueError('Preserve previous private administration plan')
        vnet=request('GET',VNET+'?api-version='+NET_API);check_prefixes(vnet)
        sku=az('vm','list-skus','-l','eastasia','--resource-type','virtualMachines','--size',SIZE,'--all')
        if len(sku)!=1 or sku[0]['name']!=SIZE or sku[0]['restrictions']:raise ValueError('Exact small VM SKU unavailable')
        usage=az('vm','list-usage','--location','eastasia')
        quotas={x['name']['value'].lower():x for x in usage}
        for key in ('cores',sku[0]['family'].lower()):
            if key not in quotas or int(quotas[key]['limit'])-int(quotas[key]['currentValue'])<2:raise ValueError('Two-core quota unavailable')
        image=az('vm','image','show','--location','eastasia','--urn','Canonical:ubuntu-24_04-lts:server:latest')
        if image.get('plan') or image['hyperVGeneration']!='V2':raise ValueError('Supported marketplace image without extra terms required')
        if az('role','definition','list','--name','Virtual Machine Administrator Login','--query','[0].name')!=ROLE:raise ValueError('Exact restricted login role differs')
        public_key=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode()
        value=template(public_key,image['name']);ids=expected_ids(value)
        import concurrent.futures
        def absent(path):return request('GET',path+'?api-version='+('2022-04-01' if '/roleAssignments/' in path else COMPUTE_API if '/Microsoft.Compute/' in path else NET_API),missing=True) is None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            if not all(pool.map(absent,ids)):raise ValueError('Preserve pre-existing resource/name collision')
        if request('GET',DEPLOYMENT+ARM_API,missing=True) is not None:raise ValueError('Preserve previous Azure deployment')
        boundaries=[]
        for path,api,keys in ((rid('Microsoft.ContainerRegistry/registries','ibkrv2stgauhecwc4itaoo'),'2023-07-01',('publicNetworkAccess','networkRuleSet','adminUserEnabled')),(rid('Microsoft.Storage/storageAccounts','ibkrv2stgauhecwc4itaoo'),'2023-05-01',('publicNetworkAccess','networkAcls','allowBlobPublicAccess')),(rid('Microsoft.KeyVault/vaults','ibkrv2stg-auhecwc4itaoo'),'2023-07-01',('publicNetworkAccess','networkAcls','enableRbacAuthorization'))):
            actual=request('GET',path+'?api-version='+api);boundaries.append({'id':path,'api':api,'properties':{key:actual['properties'].get(key) for key in keys}})
        ROOT.mkdir();record={'template':value,'resource_ids':sorted(ids),'existing_network_scope':existing_network_scope(vnet),'public_boundaries':boundaries,'owner':OWNER,'vm_size':SIZE,'image_version':image['name'],'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'authorization':'Owner explicitly approved private administration/build VM and Bastion in rg-ibkr-database-v11 on 2026-10-08; no public access restriction until tested','bootstrap_private_key_retained':False,'vm_public_ip':False,'credentials_or_production_data_changed':False,'purpose':'Private administration/build; NAT provides outbound updates only; Bastion provides Entra-controlled HTTPS/SSH entry','prepared_at':datetime.now(timezone.utc).isoformat(),'public_access_restriction_allowed':False}
        save(ROOT,'plan',record);(ROOT/'template.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print('Prepared additive private VM/Bastion plan; no live changes');return
    plan=read(ROOT,'plan')
    if plan['controller_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Frozen private administration controller drift')
    verify_boundaries(plan)
    if args.stage=='validate':
        result=az('deployment','group','validate','-g',RG,'-n',DEPLOYMENT_NAME,'--template-file',str(ROOT/'template.json'))
        if result.get('error'):raise ValueError('Azure deployment validation failed')
        if not (ROOT/'validated.json').exists():save(ROOT,'validated',{'template_sha256':hashlib.sha256((ROOT/'template.json').read_bytes()).hexdigest(),'status':'passed'})
        print('Azure additive deployment validation passed');return
    if args.stage=='what-if':
        read(ROOT,'validated');result=az('deployment','group','what-if','-g',RG,'-n',DEPLOYMENT_NAME,'--template-file',str(ROOT/'template.json'),'--no-pretty-print')
        if result.get('status')!='Succeeded':raise ValueError('What-if not succeeded')
        safe_changes(result['changes'],plan['resource_ids'])
        if not (ROOT/'what-if-qualified.json').exists():save(ROOT,'what-if-qualified',{'status':'passed','changes':[{'resourceId':c['resourceId'],'changeType':c['changeType']} for c in result['changes']],'template_sha256':hashlib.sha256((ROOT/'template.json').read_bytes()).hexdigest()})
        print('What-if confirms only approved new resources');return
    if args.stage=='deploy':
        read(ROOT,'what-if-qualified')
        if json.loads((ROOT/'template.json').read_bytes())!=plan['template']:raise ValueError('Template drift')
        if (ROOT/'deploy-intent.json').exists():raise ValueError('Inspect deployment; never blindly repeat')
        save(ROOT,'deploy-intent',{'resource_ids':plan['resource_ids'],'template_sha256':hashlib.sha256((ROOT/'template.json').read_bytes()).hexdigest(),'at':datetime.now(timezone.utc).isoformat()})
        response=request('PUT',DEPLOYMENT+ARM_API,data={'properties':{'mode':'Incremental','template':plan['template']}})
        save(ROOT,'deploy-ack',{'state':response.get('properties',{}).get('provisioningState'),'id':response.get('id')});print('Dispatched additive private administration deployment; public paths unchanged');return
    deployment=request('GET',DEPLOYMENT+ARM_API,missing=True)
    status=deployment['properties']['provisioningState'] if deployment else 'NotDispatched'
    print(json.dumps({'deployment_status':status,'vm_public_ip':False,'public_access_restricted':False}),flush=True)
    if args.stage=='state' or status!='Succeeded':return
    vm=request('GET',rid('Microsoft.Compute/virtualMachines',VM)+'?api-version='+COMPUTE_API)
    nic=request('GET',rid('Microsoft.Network/networkInterfaces','ibkrv2stg-admin-nic')+'?api-version='+NET_API)
    bastion=request('GET',rid('Microsoft.Network/bastionHosts',BASTION)+'?api-version='+NET_API)
    extension=request('GET',rid('Microsoft.Compute/virtualMachines/extensions',VM+'/AADSSHLoginForLinux')+'?api-version='+COMPUTE_API)
    if any(x['properties'].get('provisioningState')!='Succeeded' for x in (vm,nic,bastion,extension)):raise ValueError('Private access components not ready')
    if any(x['properties'].get('publicIPAddress') for x in nic['properties']['ipConfigurations']) or vm['properties']['hardwareProfile']['vmSize']!=SIZE or vm['identity']['type']!='SystemAssigned' or not vm['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication']:raise ValueError('Private VM security settings differ')
    if bastion['sku']['name']!='Standard' or not bastion['properties'].get('enableTunneling') or bastion['properties'].get('enableShareableLink') or bastion['properties'].get('enableIpConnect'):raise ValueError('Restricted Bastion configuration differs')
    roles=az('role','assignment','list','--scope',vm['id'],'--query',"[?roleDefinitionName=='Virtual Machine Administrator Login'].{principalId:principalId,scope:scope}")
    if roles!=[{'principalId':OWNER,'scope':vm['id']}]:raise ValueError('Owner-only VM login role differs')
    record={'status':'infrastructure_ready','vm':vm['id'],'bastion':bastion['id'],'vm_public_ip':False,'owner_only_vm_login':True,'entra_extension_ready':True,'existing_network_and_public_access_unchanged':True,'bastion_login_tested':False,'private_build_tested':False,'public_access_restriction_allowed':False}
    if not (ROOT/'infrastructure-qualified.json').exists():save(ROOT,'infrastructure-qualified',record)
    print(json.dumps(record))

if __name__=='__main__':main()
