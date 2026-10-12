"""Add one metadata-only identity/job; existing workloads and credentials untouched.

Every mutation is create-once and archived. Unknown outcomes require readback,
not redispatch. No value-read permissions, secret rotation or public cutover.
"""
import base64,copy,gzip,hashlib,json,sys,uuid
from datetime import datetime,timezone
from pathlib import Path
import secret_metadata_monitor_20261010 as monitor
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_metadata_monitor import BASE,SUB
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings,listing
from deploy_ms_presentation_worker_release import settings_invariant
from rehearse_ms_worker_image_rollback import restore_protected
from audit_azure_runtime import az

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage22/secret-metadata-v1'
JOB='ibkrv2stg-secret-metadata'
IDENTITY=rid('Microsoft.ManagedIdentity/userAssignedIdentities','ibkrv2stg-secret-metadata-identity')
JOBPATH=rid('Microsoft.App/jobs',JOB)
API='?api-version=2026-07-01'
REGISTRY=rid('Microsoft.ContainerRegistry/registries','ibkrv2stgauhecwc4itaoo')
READER='21090545-7ca7-4776-b22c-e363652d74d2'
PULL='7f951dda-4ed3-4680-a7ca-43fe172d538d'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:59f9e53079939c478d7e484095f3f6fcdad2019870fe7338bdc4d153d6154d61'
TAGS={'application':'ibkr-portfolio-platform','purpose':'secret-metadata-only','owner':'portfolio-platform'}


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def payload():
    source=(ROOT/'scripts/secret_metadata_monitor_20261010.py').read_text(encoding='utf-8')
    tests=(ROOT/'scripts/test_secret_metadata_monitor_20261010.py').read_text(encoding='utf-8')
    code="import os,sys,types,unittest,socket,json\nfrom unittest.mock import patch\n"
    code+="m=types.ModuleType('secret_metadata_monitor_20261010');sys.modules[m.__name__]=m\nexec(compile("+repr(source)+",'secret-metadata-monitor','exec'),m.__dict__)\n"
    code+="t=types.ModuleType('secret_metadata_tests');exec(compile("+repr(tests)+",'secret-metadata-tests','exec'),t.__dict__)\n"
    code+="def deny(*a,**k):raise RuntimeError('Network forbidden during self-test')\n"
    code+="with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny):\n result=unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromModule(t))\n"
    code+="if not result.wasSuccessful() or result.testsRun!=24 or result.skipped:raise RuntimeError('Metadata monitor self-test failed')\n"
    code+="print('SECRET_METADATA_SELF_TEST '+json.dumps({'passed':True,'tests':24}),flush=True)\nm.main()\n"
    encoded=base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'metadata-monitor-bound-payload','exec'))"]


def image_gate():
    folder=ROOT/'.work/platform-upgrade-20261010-stage19/onboarding-security-v2'
    scan=json.loads((folder/'scan-completed.json').read_bytes());remote,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if scan!=remote or scan['image']!=IMAGE or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Qualified exact image required')
    return json.loads((folder/'scan-protected-receipt.json').read_bytes())


def roles(principal):
    result=[]
    for scope,role in [(rid('Microsoft.KeyVault/vaults',v),READER) for v in monitor.VAULTS]+[(REGISTRY,PULL)]:
        name=str(uuid.uuid5(uuid.NAMESPACE_URL,scope+'|'+principal+'|'+role+'|secret-metadata-v1'))
        result.append({'path':scope+'/providers/Microsoft.Authorization/roleAssignments/'+name,
            'body':{'properties':{'roleDefinitionId':'/subscriptions/'+SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+role,
                'principalId':principal,'principalType':'ServicePrincipal'}},'scope':scope})
    return result


def role_match(actual,wanted):
    p=actual['properties'];w=wanted['body']['properties']
    return all(str(p.get(k,'')).lower()==str(v).lower() for k,v in w.items()) and p.get('scope','').lower()==wanted['scope'].lower()


def verify_roles(identity):
    principal=identity['properties']['principalId'];wanted=roles(principal)
    actual=az('role','assignment','list','--assignee-object-id',principal,'--all','--include-inherited','--fill-principal-name','false')
    if len(actual)!=4 or {x['id'].lower() for x in actual}!={x['path'].lower() for x in wanted}:raise ValueError('Dedicated identity has unexpected grants')
    for role in wanted:
        if not role_match(request('GET',role['path']+'?api-version=2022-04-01'),role):raise ValueError('Role scope differs')


def unchanged(plan):
    now=bindings([r for r in current_resources() if r['name']!=JOB])
    if now!=plan['other_workloads']:raise ValueError('Existing workload changed; re-review')


def body(identity,scheduled=False):
    return {'location':'eastasia','tags':TAGS,'identity':{'type':'UserAssigned','userAssignedIdentities':{IDENTITY:{}}},
        'properties':{'environmentId':rid('Microsoft.App/managedEnvironments','ibkrv2stg-env'),'workloadProfileName':'Consumption',
            'configuration':{'triggerType':'Schedule' if scheduled else 'Manual','replicaTimeout':300,'replicaRetryLimit':0,
                ('scheduleTriggerConfig' if scheduled else 'manualTriggerConfig'):{'parallelism':1,'replicaCompletionCount':1,**({'cronExpression':'15 6 * * *'} if scheduled else {})},
                'registries':[{'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':IDENTITY}],'secrets':[]},
            'template':{'containers':[{'name':'secret-metadata','image':IMAGE,'command':['python'],'args':payload(),
                'env':[{'name':'AZURE_CLIENT_ID','value':identity['properties']['clientId']}],
                'resources':{'cpu':0.25,'memory':'0.5Gi'}}]}}}


def verify_job(job,identity,scheduled=False):
    wanted=body(identity,scheduled);p=job['properties'];w=wanted['properties'];conf=p['configuration'];wc=w['configuration']
    if job['id'].lower()!=JOBPATH.lower() or job['tags']!=TAGS or job['identity']['type']!='UserAssigned' or set(x.lower() for x in job['identity']['userAssignedIdentities'])!={IDENTITY.lower()}:raise ValueError('Job identity/ownership differs')
    for key in ('environmentId','workloadProfileName'):
        if str(p[key]).lower()!=str(w[key]).lower():raise ValueError('Environment differs')
    for key in ('triggerType','replicaTimeout','replicaRetryLimit','registries','secrets','scheduleTriggerConfig' if scheduled else 'manualTriggerConfig'):
        actual=copy.deepcopy(conf.get(key))
        if key=='secrets' and actual is None:actual=[]
        if key=='registries':
            for registry in actual:
                for default in ('username','passwordSecretRef'):
                    if registry.get(default)=='':registry.pop(default)
        if actual!=wc[key]:raise ValueError('Job configuration differs: '+key)
    containers=copy.deepcopy(p['template']['containers'])
    for container in containers:
        if container.get('resources',{}).get('ephemeralStorage')=='':container['resources'].pop('ephemeralStorage')
    if containers!=w['template']['containers'] or p['template'].get('initContainers') or p['template'].get('volumes'):raise ValueError('Job payload differs')
    if p.get('provisioningState')!='Succeeded':raise ValueError('Job not ready')


def main(stage):
    if stage=='plan':
        if (OUT/'plan.json').exists():raise ValueError('Preserve previous plan')
        checkpoint=protected(ROOT/'.work/platform-upgrade-20261010-stage21/closeout.json')
        if len(checkpoint['upgrades'])!=2:raise ValueError('Workflow checkpoint incomplete')
        for path,api in ((IDENTITY,'2023-01-31'),(JOBPATH,'2026-07-01')):
            if request('GET',path+'?api-version='+api,missing=True) is not None:raise ValueError('New monitor resource already exists')
        vaults=[]
        for name in monitor.VAULTS:
            vault=request('GET',rid('Microsoft.KeyVault/vaults',name)+'?api-version=2023-07-01')
            if vault['properties'].get('enableRbacAuthorization') is not True or vault['location'].replace(' ','').lower()!='eastasia':raise ValueError('Vault authorization/region changed')
            vaults.append({'name':name,'configuration_sha256':fingerprint(vault['properties'])})
        reader=request('GET','/subscriptions/'+SUB+'/providers/Microsoft.Authorization/roleDefinitions/'+READER+'?api-version=2022-04-01')
        perms=reader['properties']['permissions']
        if len(perms)!=1 or set(perms[0]['dataActions'])!={'Microsoft.KeyVault/vaults/*/read','Microsoft.KeyVault/vaults/secrets/readMetadata/action'} or perms[0].get('notDataActions'):raise ValueError('Metadata-only built-in role changed')
        save(OUT,'plan',{'checked_at':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(payload()[1].encode()).hexdigest(),
            'image':IMAGE,'image_receipt':image_gate(),'identity':IDENTITY,'job':JOBPATH,'vaults':vaults,
            'other_workloads':bindings(current_resources()),'checkpoint_receipt':checkpoint['protected_receipt'],
            'scope':'One new metadata-only identity and job; four scoped assignments. No secret values, data, credentials, expirations or existing workload changes.',
            'rollback':'Disable the new schedule (restore Manual); retain evidence. Existing workloads never depend on this identity.',
            'schedule':'15 6 * * * UTC, enabled only after read-only manual verification'})
        print('Metadata-only monitor plan archived; no resources created');return
    plan=protected(OUT/'plan.json')
    if plan['source_sha256']!=hashlib.sha256(payload()[1].encode()).hexdigest():raise ValueError('Frozen monitor source changed')
    unchanged(plan)
    identity=request('GET',IDENTITY+'?api-version=2023-01-31',missing=True)
    if stage=='identity':
        if identity is not None or (OUT/'identity-intent.json').exists():raise ValueError('Reconcile existing identity intent')
        definition={'location':'eastasia','tags':TAGS};save(OUT,'identity-intent',{'body':definition})
        identity=request('PUT',IDENTITY+'?api-version=2023-01-31',data=definition)
        save(OUT,'identity-ack',{'id':identity['id'],'principalId':identity['properties']['principalId'],'clientId':identity['properties']['clientId']})
        print('Created dedicated identity; no secret permissions yet');return
    if identity is None or identity['tags']!=TAGS or identity['location'].replace(' ','').lower()!='eastasia':raise ValueError('Dedicated identity unavailable')
    if stage=='roles':
        for index,role in enumerate(roles(identity['properties']['principalId'])):
            path=role['path']+'?api-version=2022-04-01';live=request('GET',path,missing=True)
            if live is not None:
                if not role_match(live,role):raise ValueError('Role assignment collision')
                continue
            if (OUT/('role-'+str(index)+'-intent.json')).exists():raise ValueError('Unknown prior role outcome; do not redispatch')
            save(OUT,'role-'+str(index)+'-intent',role);ack=request('PUT',path,data=role['body'])
            if not role_match(ack,role):raise ValueError('Role creation differs')
            save(OUT,'role-'+str(index)+'-ack',{'id':ack['id'],'scope':role['scope']})
        verify_roles(identity);print('Three vault metadata grants and one registry pull grant verified');return
    verify_roles(identity)
    if stage=='job':
        if request('GET',JOBPATH+API,missing=True) is not None or (OUT/'job-intent.json').exists():raise ValueError('Reconcile existing job')
        definition=body(identity);save(OUT,'job-intent',{'body':definition});ack=request('PUT',JOBPATH+API,data=definition)
        save(OUT,'job-ack',{'id':ack['id'],'state':ack['properties'].get('provisioningState')});print('Created manual monitor; no schedule or normal job started');return
    job=request('GET',JOBPATH+API)
    if stage=='start':
        verify_job(job,identity)
        if (OUT/'execution-intent.json').exists():raise ValueError('Inspect existing execution; never redispatch')
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped','Canceled','Cancelled') for r in listing(JOBPATH+'/executions','2026-07-01')):raise ValueError('Monitor already active')
        save(OUT,'execution-intent',{'image':IMAGE,'payload_sha256':plan['source_sha256'],'template':job['properties']['template']})
        ack=request('POST',JOBPATH+'/start'+API,data={});save(OUT,'execution-ack',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage=='read':
        verify_job(job,identity);name=protected(OUT/'execution-ack.json')['execution'];run=request('GET',JOBPATH+'/executions/'+name+API)
        print(json.dumps({'status':run['properties']['status']}))
        if run['properties']['status'] not in ('Succeeded','Failed'):return
        actual=run['properties']['template']['containers'][0];wanted=job['properties']['template']['containers'][0]
        if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args','env')):raise ValueError('Execution differs')
        prefix=monitor.PREFIX
        query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
        rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
        if len(rows)!=1:print('Awaiting monitor log ingestion');return
        result=json.loads(rows[0]['Result'])
        if run['properties']['status']!='Succeeded' or result.get('kind')!='complete' or result.get('schema')!=monitor.SCHEMA or result.get('version')!=monitor.VERSION or result.get('vault_count')!=3 or {v['vault'] for v in result['vaults']}!=set(monitor.VAULTS) or result.get('errors') or any(result.get(k)!=0 for k in ('secret_values_read','secret_values_changed','expiry_changes','permissions_changed')):
            if not (OUT/'execution-failure.json').exists():save(OUT,'execution-failure',{'execution':name,'result':result})
            raise ValueError('Metadata collection failed; do not enable schedule')
        if not (OUT/'runtime-verified.json').exists():save(OUT,'runtime-verified',{'passed':True,'execution':name,'image':IMAGE,'result':result,'payload_sha256':plan['source_sha256'],'other_workloads_unchanged':True})
        print(json.dumps({'passed':True,'vaults':3,'listed':sum(v['listed'] for v in result['vaults']),'findings':result['finding_count'],'values_read':0}));return
    if stage=='schedule':
        proof=protected(OUT/'runtime-verified.json')
        if proof.get('passed') is not True or proof['payload_sha256']!=plan['source_sha256']:raise ValueError('Accepted metadata-only runtime required')
        verify_job(job,identity)
        if (OUT/'schedule-intent.json').exists():raise ValueError('Reconcile existing schedule intent')
        scheduled=body(identity,True);save(OUT,'schedule-intent',{'body':scheduled,'rollback_body':body(identity),'runtime_receipt':proof['protected_receipt']})
        ack=request('PUT',JOBPATH+API,data=scheduled);save(OUT,'schedule-ack',{'state':ack['properties'].get('provisioningState')});print('Enabled daily 06:15 UTC metadata-only monitor');return
    if stage=='verify':
        verify_job(job,identity,True);unchanged(plan)
        if not (OUT/'schedule-verified.json').exists():save(OUT,'schedule-verified',{'passed':True,'image':IMAGE,'job':JOBPATH,'schedule':'15 6 * * *','only_scoped_metadata_and_pull_roles':True,'other_workloads_unchanged':True,'normal_scheduled_execution_observed':False})
        print('Scheduled metadata monitor verified; genuine scheduled execution still pending');return
    raise ValueError('Unknown stage')


if __name__=='__main__':main(sys.argv[1])
