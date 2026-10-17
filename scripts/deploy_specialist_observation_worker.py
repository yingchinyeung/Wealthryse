"""Dedicated observation identity preflight rollout; no live event capture enabled.

Reuses the existing manual-job configuration verifier, not the reporting
credential or identity. Only reviewed new resources are created or removed.
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
import secrets
import uuid
from urllib.parse import quote

from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB, VAULT, IMAGE
from deploy_specialist_report_worker import ROOT, validate_job

IDENTITY='ibkrv2stg-specialist-observation-identity'
IDENTITY_PATH=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/'+IDENTITY
JOB='ibkrv2stg-observe-reader'
PROVISION='ibkrv2stg-observe-prov'
SECRET='database-specialist-observation-url'
FOLDER=ROOT/'.work/specialist-observation-worker-20261002'
PURPOSE='specialist-observation-acceptance'
# The readers moved to the security-patched ibkr-platform image on 10 Oct
# (scripts/control_upgrade_readers_20261010.py), as deploy/deployed-images.json records.
# The temporary provisioner keeps the image it was first built with.
READER_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:3e9841a5abc3a37592a7c4134f07040db2ec4103ee28f333be0304cd1c9f3ebe'


def job_path(name):
    if name not in (JOB,PROVISION) or not 2<=len(name)<=32:
        raise ValueError('Unregistered observation job')
    return BASE+'Microsoft.App/jobs/'+name


def bundle(provision=False):
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    modules=('specialist_observation_access','specialist_observation_runtime')
    for name in modules:
        code=(ROOT/('src/ibkr_etl/'+name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'observation-runtime','exec'),m.__dict__)\n"
    source+=(ROOT/'scripts'/('provision_specialist_observation_login.py' if provision else 'run_specialist_observation_worker.py')).read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-observation-worker','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def definition(provision=False):
    name=PROVISION if provision else JOB
    identity=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity' if provision else IDENTITY_PATH
    args,digest=bundle(provision)
    refs=[{'name':'observation-url','keyVaultUrl':f'https://{VAULT}.vault.azure.net/secrets/{SECRET}','identity':identity}]
    env=[{'name':'SPECIALIST_OBSERVATION_DATABASE_URL','secretRef':'observation-url'}]
    if provision:
        refs.append({'name':'admin-url','keyVaultUrl':f'https://{VAULT}.vault.azure.net/secrets/database-admin-url','identity':identity})
        env.append({'name':'DATABASE_URL','secretRef':'admin-url'})
    return {'location':'eastasia','identity':{'type':'UserAssigned','userAssignedIdentities':{identity:{}}},
        'tags':{'purpose':PURPOSE,'mode':'manual-preflight','payloadSha256':digest},
        'properties':{'environmentId':BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env',
        'configuration':{'triggerType':'Manual','replicaTimeout':180,'replicaRetryLimit':0,
            'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},'secrets':refs,
            'registries':[{'server':'ibkrv2stgauhecwc4itaoo.azurecr.io','identity':identity}]},
        'template':{'containers':[{'name':name,'image':IMAGE if provision else READER_IMAGE,'command':['python'],'args':args,'env':env,
            'resources':{'cpu':0.5,'memory':'1Gi'}}]}}}


def validate(current,provision=False):
    wanted=definition(provision)
    validate_job(current,wanted=wanted)
    if current.get('identity',{}).get('type')!='UserAssigned':
        raise ValueError('Unexpected additional observation identity')
    if current['properties'].get('environmentId','').lower()!=wanted['properties']['environmentId'].lower():
        raise ValueError('Unexpected observation environment')
    if current['properties']['template'].get('initContainers'):
        raise ValueError('Unregistered observation init container')
    resources=current['properties']['template']['containers'][0].get('resources',{})
    if any(resources.get(k)!=v for k,v in {'cpu':0.5,'memory':'1Gi'}.items()):
        raise ValueError('Unexpected observation worker capacity')


def idle(name):
    runs=request('GET',job_path(name)+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
        raise ValueError('Observation job busy or unknown execution state')


def verify_cloud_identity():
    identity=request('GET',IDENTITY_PATH+'?api-version=2023-01-31')
    if identity.get('tags',{}).get('purpose')!=PURPOSE:
        raise ValueError('Unregistered observation identity')
    principal=str(uuid.UUID(identity['properties']['principalId']))
    grants=az('role','assignment','list','--all','--include-inherited','--query',
        "[?principalId=='"+principal+"'].{scope:scope,role:roleDefinitionId}")
    expected={(scope.lower(),role) for scope,role in [
        (BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo','7f951dda-4ed3-4680-a7ca-43fe172d538d'),
        (BASE+f'Microsoft.KeyVault/vaults/{VAULT}/secrets/{SECRET}','4633458b-17de-408a-b874-0445c86b69e6')]}
    if len(grants)!=2 or {(g['scope'].lower(),g['role'].rsplit('/',1)[-1]) for g in grants}!=expected:
        raise ValueError('Observation managed identity RBAC differs from exact registry/secret scope')
    return dict(principal_id=principal,client_id=identity['properties']['clientId'],registered_rbac_assignments=2)


def verify_execution(name,execution):
    if not re.fullmatch(re.escape(name)+r'-[a-z0-9]{7}',execution or ''):
        raise ValueError('Exact observation execution required')
    provision=name==PROVISION
    validate(request('GET',job_path(name)+'?api-version=2024-03-01'),provision)
    run=request('GET',job_path(name)+'/executions/'+execution+'?api-version=2024-03-01')
    actual=run['properties'].get('template',{}).get('containers',[])
    wanted=definition(provision)['properties']['template']['containers'][0]
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('image','command','args')):
        raise ValueError('Observation execution differs from reviewed source/image')
    env=actual[0].get('env',[])
    if len(env)!=len(wanted['env']) or any(a.get('name')!=e['name'] or a.get('secretRef') not in
        (e['secretRef'],'cappjob-'+name) for a,e in zip(env,wanted['env'])):
        raise ValueError('Observation execution credential bindings differ')
    prefix='SPECIALIST_OBSERVATION_PROVISION ' if provision else 'SPECIALIST_OBSERVATION_WORKER '
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"' | where Log_s startswith '"+prefix+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(prefix)) for r in logs]
    result={'execution':execution,'status':run['properties']['status'],'payload_sha256':bundle(provision)[1],
            'configuration_verified':True,'identity':IDENTITY if not provision else 'existing-provisioning-identity',
            'records':records}
    if not provision: result['cloud_identity']=verify_cloud_identity()
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/(execution+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    if result['status']!='Succeeded' or len(records)!=1 or records[0].get('status')!='passed':
        raise ValueError('Successful authenticated observation access evidence required')


def prepare():
    for name in (JOB,PROVISION):
        existing=request('GET',job_path(name)+'?api-version=2024-03-01',missing=True)
        if existing: raise ValueError('Observation job exists; inspect before preparing')
    identity=request('GET',IDENTITY_PATH+'?api-version=2023-01-31',missing=True)
    secret_metadata=az('keyvault','secret','list','--vault-name',VAULT,'--query',"[?name=='"+SECRET+"'].{name:name,enabled:attributes.enabled,tags:tags}")
    if identity and identity.get('tags',{}).get('purpose')!=PURPOSE:
        raise ValueError('Existing observation identity is not registered')
    if secret_metadata and (not identity or len(secret_metadata)!=1 or secret_metadata[0].get('enabled') is not True
                            or secret_metadata[0].get('tags',{}).get('purpose')!=PURPOSE):
        raise ValueError('Existing observation secret requires investigation; never rotate implicitly')
    if not identity:
        identity=request('PUT',IDENTITY_PATH+'?api-version=2023-01-31',data={'location':'eastasia','tags':{'purpose':PURPOSE}})
    if not secret_metadata:
        password=secrets.token_urlsafe(48)
        value=f'postgresql://ibkr_specialist_observation_login:{quote(password,safe="")}@ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com:5432/ibkr_staging?sslmode=require'
        request('PUT',f'https://{VAULT}.vault.azure.net/secrets/{SECRET}?api-version=7.4',vault=True,
                data={'value':value,'contentType':'application/x-postgresql-connection-string','tags':{'purpose':PURPOSE}})
        del value,password
    for scope,role in [(BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo','7f951dda-4ed3-4680-a7ca-43fe172d538d'),
                       (BASE+f'Microsoft.KeyVault/vaults/{VAULT}/secrets/{SECRET}','4633458b-17de-408a-b874-0445c86b69e6')]:
        assignment=str(uuid.uuid5(uuid.NAMESPACE_URL,IDENTITY_PATH+scope+role))
        path=scope+'/providers/Microsoft.Authorization/roleAssignments/'+assignment+'?api-version=2022-04-01'
        existing=request('GET',path,missing=True)
        if not existing:
            request('PUT',path,data={'properties':{'principalId':identity['properties']['principalId'],'principalType':'ServicePrincipal',
                    'roleDefinitionId':f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}})
            existing=request('GET',path)
        p=existing['properties']
        if p['principalId']!=identity['properties']['principalId'] or not p['roleDefinitionId'].endswith('/'+role) or p['scope'].lower()!=scope.lower():
            raise ValueError('Observation grant readback mismatch')
    deploy(True)
    print(json.dumps({'prepared':PROVISION,'identity':IDENTITY,'secret_exposed':False,'storage_permissions_added':False}))


def deploy(provision=False):
    name=PROVISION if provision else JOB
    if request('GET',job_path(name)+'?api-version=2024-03-01',missing=True):
        raise ValueError('Observation job already exists; no implicit replacement')
    definition_value=definition(provision)
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/(name+'-definition.json')).write_text(json.dumps(definition_value,indent=2),encoding='utf-8')
    request('PUT',job_path(name)+'?api-version=2024-03-01',data=definition_value)


def update():
    """Give the installed reader this checkout's code. Everything else about the job (image,
    identity, credential bindings, limits) must already match the reviewed definition, so
    the PUT changes only the bundled code and its payloadSha256 tag."""
    current=request('GET',job_path(JOB)+'?api-version=2024-03-01')
    if current.get('tags',{}).get('purpose')!=PURPOSE:
        raise ValueError('Unregistered observation job')
    wanted=definition();digest=wanted['tags']['payloadSha256']
    containers=current['properties']['template'].get('containers') or []
    before=current.get('tags',{}).get('payloadSha256')
    if before==digest:
        validate(current)
        print(json.dumps({'updated':False,'job':JOB,'payload_sha256':digest}))
        return
    reviewed=wanted['properties']['template']['containers'][0]
    differ=[k for k in ('image','command','env') if len(containers)==1 and containers[0].get(k)!=reviewed[k]]
    if differ:
        raise ValueError('Observation job differs beyond its code: '+', '.join(differ))
    candidate=json.loads(json.dumps(current))
    if len(containers)==1:
        candidate['properties']['template']['containers'][0]['args']=wanted['properties']['template']['containers'][0]['args']
    validate(candidate)
    idle(JOB)
    wanted['tags']={**current['tags'],'payloadSha256':digest}
    if current['properties'].get('workloadProfileName'):
        wanted['properties']['workloadProfileName']=current['properties']['workloadProfileName']
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/(JOB+'-before-'+str(before)[:12]+'.json')).write_text(json.dumps(current,indent=2),encoding='utf-8')
    (FOLDER/(JOB+'-definition-'+digest[:12]+'.json')).write_text(json.dumps(wanted,indent=2),encoding='utf-8')
    request('PUT',job_path(JOB)+'?api-version=2024-03-01',data=wanted)
    after=request('GET',job_path(JOB)+'?api-version=2024-03-01')
    validate(after)
    if after.get('tags',{}).get('payloadSha256')!=digest:
        raise ValueError('Observation job readback differs from the update')
    print(json.dumps({'updated':True,'job':JOB,'payload_sha256_before':before,'payload_sha256':digest,'image':READER_IMAGE}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare','provision','install','update','test','verify','cleanup-provisioner'])
    parser.add_argument('--execution')
    args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    if args.stage=='prepare': prepare()
    elif args.stage in ('provision','test'):
        name=PROVISION if args.stage=='provision' else JOB
        validate(request('GET',job_path(name)+'?api-version=2024-03-01'),name==PROVISION)
        idle(name)
        if args.stage=='test': verify_cloud_identity()
        print(json.dumps({'execution':request('POST',job_path(name)+'/start?api-version=2024-03-01')['name']}))
    elif args.stage=='update': update()
    elif args.stage=='install':
        verify_execution(PROVISION,args.execution)
        deploy()
        print(json.dumps({'installed':JOB,'mode':'manual-access-verification-only'}))
    elif args.stage=='verify':
        verify_execution(PROVISION if (args.execution or '').startswith(PROVISION+'-') else JOB,args.execution)
    else:
        verify_execution(JOB,args.execution)
        validate(request('GET',job_path(PROVISION)+'?api-version=2024-03-01'),True)
        idle(PROVISION)
        request('DELETE',job_path(PROVISION)+'?api-version=2024-03-01')
        print(json.dumps({'temporary_provisioner_removed':PROVISION,'definition_and_evidence_retained':True}))


if __name__=='__main__': main()
