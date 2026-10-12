"""Protected intent -> one manual typed enrollment -> independent readback.

The temporary job carries existing administrative credentials for this fixed
operation only. Retain evidence and remove it after successful verification.
"""
import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from audit_azure_runtime import az
from deploy_metadata_monitor import request,BASE,VAULT,SUB
from deploy_specialist_report_worker import validate_job
from ibkr_etl.specialist_archive_reader import ACCOUNT,CONTAINER,SCOPE
from ibkr_etl.specialist_audit_service import validate_archive_properties
from ibkr_etl.agent_observation_evidence import AGENT_PRINCIPAL
from ibkr_etl.specialist_authority_runtime import TENANT
from enroll_specialist_gateway_authority import verify_intent

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/specialist-gateway-20261002'
JOB='ibkrv2stg-gateway-enroll'
PATH=BASE+'Microsoft.App/jobs/'+JOB
IDENTITY=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity'
IMAGE=ACCOUNT+'.azurecr.io/specialist-gateway@sha256:21f183cc51b1502e14f4868db63d0b3b5ce0ac435d638997c827e337eec5ac68'


def retain(value,kind):
    payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(payload).hexdigest()
    validate_archive_properties(request('GET',SCOPE+'?api-version=2023-05-01'))
    service=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential())
    name=f'acceptance/gateway-enrollment-{kind}-{digest}.json';blob=service.get_blob_client(CONTAINER,name)
    try:blob.upload_blob(payload,overwrite=False)
    except ResourceExistsError:pass
    props=blob.get_blob_properties()
    if not props.version_id or props.size!=len(payload) or service.get_blob_client(CONTAINER,name,version_id=props.version_id).download_blob(offset=0,length=len(payload)+1).readall()!=payload:
        raise ValueError('Protected enrollment receipt differs')
    validate_archive_properties(request('GET',SCOPE+'?api-version=2023-05-01'))
    return dict(blob=name,sha256=digest,version_id=props.version_id,protected_readback_verified=True)


def definition(intent):
    verify_intent(intent)
    source=(ROOT/'scripts/enroll_specialist_gateway_authority.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=intent['source_sha256']:raise ValueError('Enrollment source changed')
    refs=[dict(name='admin-url',keyVaultUrl=f'https://{VAULT}.vault.azure.net/secrets/database-admin-url',identity=IDENTITY),
        dict(name='authority-url',keyVaultUrl=f'https://{VAULT}.vault.azure.net/secrets/database-specialist-authority-url',identity=IDENTITY)]
    env=[dict(name='DATABASE_URL',secretRef='admin-url'),dict(name='SPECIALIST_AUTHORITY_DATABASE_URL',secretRef='authority-url'),
        dict(name='GATEWAY_ENROLLMENT_INTENT',value=json.dumps(intent,sort_keys=True,separators=(',',':')))]
    return dict(location='eastasia',identity=dict(type='UserAssigned',userAssignedIdentities={IDENTITY:{}}),
        tags=dict(purpose='temporary-typed-gateway-enrollment',operationId=intent['operation_id']),
        properties=dict(environmentId=BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env',
            configuration=dict(triggerType='Manual',replicaTimeout=180,replicaRetryLimit=0,
                manualTriggerConfig=dict(parallelism=1,replicaCompletionCount=1),secrets=refs,
                registries=[dict(server=ACCOUNT+'.azurecr.io',identity=IDENTITY)]),
            template=dict(containers=[dict(name=JOB,image=IMAGE,command=['python'],args=['-c',source],env=env,
                resources=dict(cpu=0.5,memory='1Gi'))])))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','start','verify','cleanup']);parser.add_argument('--execution');args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    path=FOLDER/'authority-intent.json'
    if args.stage=='prepare':
        if path.exists() or request('GET',PATH+'?api-version=2024-03-01',missing=True):raise ValueError('Enrollment intent/job exists; inspect instead of replacing')
        owner=json.loads((FOLDER/'owner-authentication-acceptance.json').read_text())
        if owner.get('status')!='passed' or owner.get('real_entra_signature_verified') is not True or owner.get('registered_client_verified') is not True:raise ValueError('Real owner sign-in acceptance required')
        intent=dict(schema='specialist-gateway-authority-enrollment-v1',operation_id=str(uuid.uuid4()),owner_sha256=owner['user_sha256'],
            source_sha256=hashlib.sha256((ROOT/'scripts/enroll_specialist_gateway_authority.py').read_bytes()).hexdigest(),
            tenant_id=TENANT,principal_id=AGENT_PRINCIPAL,subject_ids=['U16088571'],tool_names=['get_performance_series'],
            expires_at=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat())
        wanted=definition(intent)
        receipt=retain(dict(intent=intent,definition=wanted,owner_authentication=owner),'intent')
        path.write_text(json.dumps(intent,sort_keys=True,indent=2),encoding='utf-8')
        (FOLDER/'authority-protected-intent.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        (FOLDER/'authority-definition.json').write_text(json.dumps(wanted,indent=2),encoding='utf-8')
        request('PUT',PATH+'?api-version=2024-03-01',data=wanted)
        print(json.dumps(dict(prepared=JOB,protected_intent=receipt,subjects=intent['subject_ids'],tools=intent['tool_names'],background_allowed=False)));return
    intent=json.loads(path.read_text());wanted=definition(intent)
    current=request('GET',PATH+'?api-version=2024-03-01');validate_job(current,wanted=wanted)
    runs=request('GET',PATH+'/executions?api-version=2024-03-01').get('value',[])
    if args.stage=='start':
        if runs:raise ValueError('Enrollment was already dispatched; inspect exact execution, no automatic retry')
        result=request('POST',PATH+'/start?api-version=2024-03-01')
        dispatch=dict(execution=result['name'],operation_id=intent['operation_id'],source_sha256=intent['source_sha256'])
        (FOLDER/'authority-dispatch.json').write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch));return
    dispatch=json.loads((FOLDER/'authority-dispatch.json').read_text())
    if args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or ''):raise ValueError('Exact enrollment execution required')
    run=request('GET',PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    containers=run['properties'].get('template',{}).get('containers',[]);container=wanted['properties']['template']['containers'][0]
    if len(containers)!=1 or any(containers[0].get(k)!=container[k] for k in ('image','command','args')):raise ValueError('Enrollment execution source differs')
    if run['properties']['status']!='Succeeded':raise ValueError('Enrollment has not succeeded; inspect before retry')
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith 'SPECIALIST_GATEWAY_ENROLLMENT ' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix('SPECIALIST_GATEWAY_ENROLLMENT ')) for r in logs]
    if (len(records)!=1 or records[0].get('status')!='passed' or records[0].get('operation_id')!=intent['operation_id']
        or records[0].get('fresh_authority_verified') is not True or records[0].get('source_rows_changed')!=0
        or records[0].get('background_allowed') is not False or records[0].get('existing_authority_overwritten') is not False):
        raise ValueError('Fresh authority enrollment acceptance unavailable')
    receipt=retain(dict(intent=intent,dispatch=dispatch,record=records[0],configuration_verified=True),'completed')
    result=dict(execution=args.execution,status='passed',record=records[0],protected_receipt=receipt)
    (FOLDER/'authority-acceptance.json').write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    if args.stage=='cleanup':
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):raise ValueError('Enrollment job not idle')
        request('DELETE',PATH+'?api-version=2024-03-01')
        if request('GET',PATH+'?api-version=2024-03-01',missing=True) is not None:raise ValueError('Enrollment cleanup pending')
        result['temporary_job_removed']=True
    print(json.dumps(result))


if __name__=='__main__':main()
