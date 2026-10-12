"""Staged, manual-only reporting identity rollout. No schedules or email."""
import argparse
import base64
import hashlib
import json
import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, BASE, SUB, VAULT, IMAGE

ROOT=Path(__file__).resolve().parents[1]
IDENTITY='ibkrv2stg-specialist-report-identity'
JOB='ibkrv2stg-agent-report'
PROVISION='ibkrv2stg-agent-report-provision'
SECRET='database-specialist-report-url'
IDENTITY_PATH=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/'+IDENTITY
FOLDER=ROOT/'.work/specialist-report-worker-20261002'


def job_path(name):
    if name not in (JOB,PROVISION) or not 2<=len(name)<=32: raise ValueError('Unregistered or invalid reporting job name')
    return BASE+'Microsoft.App/jobs/'+name


def bundle(provision=False):
    code=(ROOT/'src/ibkr_etl/specialist_report_runtime.py').read_text(encoding='utf-8')
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    source+="m=types.ModuleType('ibkr_etl.specialist_report_runtime');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'report-runtime','exec'),m.__dict__)\n"
    source+=(ROOT/'scripts'/('provision_specialist_report_login.py' if provision else 'run_specialist_report_worker.py')).read_text(encoding='utf-8')
    encoded=base64.b64encode(source.encode()).decode()
    return ['-c',"import base64;exec(compile(base64.b64decode('"+encoded+"'),'specialist-report-worker','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def definition(provision=False):
    name=PROVISION if provision else JOB
    identity=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity' if provision else IDENTITY_PATH
    args,digest=bundle(provision)
    refs=[{'name':'report-url','keyVaultUrl':f'https://{VAULT}.vault.azure.net/secrets/{SECRET}','identity':identity}]
    env=[{'name':'SPECIALIST_REPORT_DATABASE_URL','secretRef':'report-url'}]
    if provision:
        refs.append({'name':'admin-url','keyVaultUrl':f'https://{VAULT}.vault.azure.net/secrets/database-admin-url','identity':identity})
        env.append({'name':'DATABASE_URL','secretRef':'admin-url'})
    return {'location':'eastasia','identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},
        'tags':{'purpose':'specialist-report-acceptance','mode':'manual-preflight','payloadSha256':digest},
        'properties':{'environmentId':BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env',
        'configuration':{'triggerType':'Manual','replicaTimeout':180,'replicaRetryLimit':0,
            'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},'secrets':refs,
            'registries':[{'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':identity}]},
        'template':{'containers':[{'name':name,'image':IMAGE,'command':['python'],'args':args,'env':env,
            'resources':{'cpu':0.5,'memory':'1Gi'}}]}}}


def validate_job(current,provision=False,*,wanted=None):
    wanted=definition(provision) if wanted is None else wanted
    if {v.lower() for v in current.get('identity',{}).get('userAssignedIdentities',{})}!={v.lower() for v in wanted['identity']['userAssignedIdentities']}:
        raise ValueError('Unexpected worker identity')
    actual=current['properties']['configuration']; target=wanted['properties']['configuration']
    for key in ('triggerType','replicaTimeout','replicaRetryLimit','manualTriggerConfig'):
        if actual.get(key)!=target[key]: raise ValueError('Unexpected worker execution controls')
    def bindings(items): return sorted((i.get('name'),i.get('keyVaultUrl'),i.get('identity','').lower()) for i in items)
    if bindings(actual.get('secrets',[]))!=bindings(target['secrets']): raise ValueError('Unexpected credential bindings')
    containers=current['properties']['template']['containers']
    expected=wanted['properties']['template']['containers'][0]
    if len(containers)!=1 or any(containers[0].get(key)!=expected[key] for key in ('image','command','args','env')):
        raise ValueError('Worker source/image/environment differs from reviewed payload')
    if current['properties']['template'].get('volumes'):
        raise ValueError('Unregistered worker volumes')


def verify_execution(name,execution):
    if not re.fullmatch(re.escape(name)+r'-[a-z0-9]{7}',execution or ''): raise ValueError('Exact execution required')
    provision=name==PROVISION
    current=request('GET',job_path(name)+'?api-version=2024-03-01')
    validate_job(current,provision)
    run=request('GET',job_path(name)+'/executions/'+execution+'?api-version=2024-03-01')
    containers=run['properties'].get('template',{}).get('containers',[])
    expected=definition(provision)['properties']['template']['containers'][0]
    if len(containers)!=1 or any(containers[0].get(k)!=expected[k] for k in ('image','command','args')):
        raise ValueError('Executed source/image differs')
    actual=containers[0].get('env',[])
    if len(actual)!=len(expected['env']) or any(a.get('name')!=e['name'] or a.get('secretRef') not in {e['secretRef'],'cappjob-'+name}
        for a,e in zip(actual,expected['env'])): raise ValueError('Executed credentials differ')
    prefix='SPECIALIST_REPORT_PROVISION ' if provision else 'SPECIALIST_REPORT_WORKER '
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"' | where Log_s startswith '"+prefix+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(prefix)) for r in logs]
    result={'execution':execution,'status':run['properties']['status'],'payload_sha256':bundle(provision)[1],'configuration_verified':True,
            'identity':IDENTITY if not provision else 'existing-provisioning-identity','records':records}
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/(execution+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['status']!='Succeeded' or not records or any(r.get('status')!='passed' for r in records):
        raise ValueError('Successful authenticated acceptance evidence required')
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare','resume-prepare','provision','install','test','verify','cleanup-provisioner'])
    parser.add_argument('--execution')
    args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    if args.stage=='prepare':
        for path in (job_path(JOB),job_path(PROVISION)):
            if request('GET',path+'?api-version=2024-03-01',missing=True): raise ValueError('Reporting job exists; inspect instead of replacing')
        if request('GET',IDENTITY_PATH+'?api-version=2023-01-31',missing=True): raise ValueError('Reporting identity exists; inspect before resuming')
        if SECRET in az('keyvault','secret','list','--vault-name',VAULT,'--query','[].name'):
            raise ValueError('Reporting credential exists; no implicit rotation')
        identity=request('PUT',IDENTITY_PATH+'?api-version=2023-01-31',data={'location':'eastasia','tags':{'purpose':'specialist-report-acceptance'}})
        password=secrets.token_urlsafe(48)
        value=f'postgresql://ibkr_specialist_report_login:{quote(password,safe="")}@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com:5432/ibkr_staging?sslmode=require'
        request('PUT',f'https://{VAULT}.vault.azure.net/secrets/{SECRET}?api-version=7.4',vault=True,
            data={'value':value,'contentType':'application/x-postgresql-connection-string','tags':{'purpose':'restricted-specialist-reporting'}})
        del value,password
        for scope,role in [(BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo','7f951dda-4ed3-4680-a7ca-43fe172d538d'),
                           (BASE+f'Microsoft.KeyVault/vaults/{VAULT}/secrets/{SECRET}','4633458b-17de-408a-b874-0445c86b69e6')]:
            assignment=str(uuid.uuid5(uuid.NAMESPACE_URL,IDENTITY_PATH+scope+role))
            request('PUT',scope+'/providers/Microsoft.Authorization/roleAssignments/'+assignment+'?api-version=2022-04-01',
                data={'properties':{'principalId':identity['properties']['principalId'],'principalType':'ServicePrincipal',
                    'roleDefinitionId':f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}})
        request('PUT',job_path(PROVISION)+'?api-version=2024-03-01',data=definition(True))
        print(json.dumps({'prepared':PROVISION,'secret_exposed':False,'role_scopes':'exact registry and exact new secret only'}))
    elif args.stage=='resume-prepare':
        # Recover preparation after a rejected job definition without rotating
        # the credential or recreating/replacing the existing identity.
        identity=request('GET',IDENTITY_PATH+'?api-version=2023-01-31')
        if identity.get('tags',{}).get('purpose')!='specialist-report-acceptance':
            raise ValueError('Unexpected existing identity')
        names=az('keyvault','secret','list','--vault-name',VAULT,'--query',"[?name=='"+SECRET+"' && attributes.enabled==`true`].name")
        if names!=[SECRET]: raise ValueError('Prepared active credential not found')
        for scope,role in [(BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo','7f951dda-4ed3-4680-a7ca-43fe172d538d'),
                           (BASE+f'Microsoft.KeyVault/vaults/{VAULT}/secrets/{SECRET}','4633458b-17de-408a-b874-0445c86b69e6')]:
            assignment=str(uuid.uuid5(uuid.NAMESPACE_URL,IDENTITY_PATH+scope+role))
            existing=request('GET',scope+'/providers/Microsoft.Authorization/roleAssignments/'+assignment+'?api-version=2022-04-01')['properties']
            if existing['principalId']!=identity['properties']['principalId'] or not existing['roleDefinitionId'].endswith('/'+role) or existing['scope'].lower()!=scope.lower():
                raise ValueError('Prepared role scope differs')
        if request('GET',job_path(PROVISION)+'?api-version=2024-03-01',missing=True):
            raise ValueError('Provisioning job already exists; inspect instead')
        request('PUT',job_path(PROVISION)+'?api-version=2024-03-01',data=definition(True))
        print(json.dumps({'prepared':PROVISION,'credential_rotated':False,'existing_identity_preserved':True}))
    elif args.stage in ('provision','test'):
        name=PROVISION if args.stage=='provision' else JOB
        current=request('GET',job_path(name)+'?api-version=2024-03-01')
        validate_job(current,name==PROVISION)
        runs=request('GET',job_path(name)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Reporting job busy')
        result=request('POST',job_path(name)+'/start?api-version=2024-03-01')
        print(json.dumps({'execution':result['name']}))
    elif args.stage=='install':
        verify_execution(PROVISION,args.execution)
        if request('GET',job_path(JOB)+'?api-version=2024-03-01',missing=True): raise ValueError('Worker already exists; no implicit replacement')
        request('PUT',job_path(JOB)+'?api-version=2024-03-01',data=definition())
        print(json.dumps({'installed':JOB,'mode':'manual-access-verification-only','retry':0}))
    elif args.stage=='verify':
        name=PROVISION if (args.execution or '').startswith(PROVISION+'-') else JOB
        verify_execution(name,args.execution)
    else:
        verify_execution(JOB,args.execution)
        current=request('GET',job_path(PROVISION)+'?api-version=2024-03-01')
        validate_job(current,True)
        runs=request('GET',job_path(PROVISION)+'/executions?api-version=2024-03-01').get('value',[])
        if not runs or any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
            raise ValueError('Provisioning job not terminal')
        request('DELETE',job_path(PROVISION)+'?api-version=2024-03-01')
        print(json.dumps({'removed_temporary_provisioner':PROVISION,'preserved':'restricted login, identity, secret, worker, logs and evidence'}))


if __name__=='__main__': main()
