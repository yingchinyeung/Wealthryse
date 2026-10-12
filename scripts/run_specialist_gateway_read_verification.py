"""One manual independent read-only acceptance, with protected dispatch intent.

Preserves the installed reporting worker, scopes and schedule. Never sends email
or borrows the observation/audit credentials. A dispatch is not retried.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from audit_azure_runtime import az
from deploy_metadata_monitor import request, SUB
from deploy_specialist_report_worker import ROOT, JOB, job_path, validate_job
from run_specialist_report_preview import grants, settings_hash
from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER, SCOPE
from ibkr_etl.specialist_audit_service import validate_archive_properties

FOLDER = ROOT / '.work/specialist-gateway-20261002'
PREFIX = 'SPECIALIST_GATEWAY_READ_VERIFIED '
MODULES = ('agent_governance','specialist_autonomy','agent_daily_schedule','agent_daily_outbox',
           'agent_daily_report','specialist_report_runtime','specialist_archive_reader',
           'specialist_audit','specialist_audit_service','specialist_audit_runtime',
           'agent_observation_evidence','agent_observation_audit','specialist_evidence')


def validate_owner(owner):
    expected = dict(status='passed',subject_id='U16088571',tool_name='get_performance_series',
                    account_read_status=200,real_entra_signature_verified=True,
                    registered_client_verified=True,invalid_signature_status=401,
                    financial_values_logged=False,token_retained=False)
    if (any(type(owner.get(k)) is not type(v) or owner.get(k)!=v for k,v in expected.items())
        or type(owner.get('observations')) is not int or owner['observations']<1
        or any(not re.fullmatch('[0-9a-f]{64}',owner.get(k,'')) for k in ('response_sha256','user_sha256'))):
        raise ValueError('Successful real owner read acceptance required')


def bundle(owner):
    validate_owner(owner)
    source = "import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in MODULES:
        code = (ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source += "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'read-verification','exec'),m.__dict__)\n"
    source += 'EXPECTED_RESPONSE_SHA256='+repr(owner['response_sha256'])+'\n'
    source += 'EXPECTED_USER_SHA256='+repr(owner['user_sha256'])+'\n'
    source += (ROOT/'scripts/verify_specialist_gateway_read.py').read_text(encoding='utf-8')
    encoded = base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'gateway-read-verification','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def template(job,owner):
    validate_job(job)
    value = dict(containers=copy.deepcopy(job['properties']['template']['containers']))
    value['containers'][0]['args'] = bundle(owner)[0]
    value['containers'][0]['resources'] = dict(cpu=0.5,memory='1Gi')
    return value


def retain(value,kind):
    payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    digest=hashlib.sha256(payload).hexdigest()
    validate_archive_properties(request('GET',SCOPE+'?api-version=2023-05-01'))
    service=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential())
    name=f'acceptance/gateway-read-{kind}-{digest}.json'
    blob=service.get_blob_client(CONTAINER,name)
    try: blob.upload_blob(payload,overwrite=False)
    except ResourceExistsError: pass
    props=blob.get_blob_properties()
    if (not props.version_id or props.size!=len(payload)
        or service.get_blob_client(CONTAINER,name,version_id=props.version_id).download_blob(offset=0,length=len(payload)+1).readall()!=payload):
        raise ValueError('Protected read acceptance differs')
    validate_archive_properties(request('GET',SCOPE+'?api-version=2023-05-01'))
    return dict(blob=name,sha256=digest,version_id=props.version_id,protected_readback_verified=True)


def verify_container(actual,wanted):
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('image','command','args')):
        raise ValueError('Executed independent verification source differs')
    if any(actual[0].get('resources',{}).get(k)!=v for k,v in wanted['resources'].items()):
        raise ValueError('Executed verification resources differ')
    env=actual[0].get('env',[])
    if (len(env)!=1 or env[0].get('name')!='SPECIALIST_REPORT_DATABASE_URL'
        or env[0].get('secretRef') not in ('report-url','cappjob-'+JOB) or 'value' in env[0]):
        raise ValueError('Executed independent verification credential differs')


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify']);p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    owner=json.loads((FOLDER/'owner-read-acceptance.json').read_text());validate_owner(owner)
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job);grants()
    wanted=template(job,owner);path=FOLDER/'read-verification-dispatch.json'
    if args.stage=='start':
        if path.exists() or (FOLDER/'read-verification-intent.json').exists():
            raise ValueError('Verification intent exists; inspect, do not redispatch')
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Reporting worker busy')
        intent=dict(owner=owner,payload_sha256=bundle(owner)[1],job_settings_sha256=settings_hash(job),template=wanted)
        protected=retain(intent,'intent')
        (FOLDER/'read-verification-intent.json').write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=wanted)
        dispatch=dict(execution=result['name'],payload_sha256=intent['payload_sha256'],job_settings_sha256=intent['job_settings_sha256'],protected_intent=protected)
        path.write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch));return
    dispatch=json.loads(path.read_text())
    if args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or ''):
        raise ValueError('Exact independent verification execution required')
    if dispatch['job_settings_sha256']!=settings_hash(job) or dispatch['payload_sha256']!=bundle(owner)[1]:
        raise ValueError('Installed worker or verification source changed')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],wanted['containers'][0])
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.stage=='inspect':
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records,
                             configuration_verified=True,job_settings_unchanged=True)));return
    if run['properties']['status']!='Succeeded': raise ValueError('Read verification not successful; inspect, no redispatch')
    if (len(records)!=1 or records[0].get('status')!='passed' or records[0].get('response_sha256')!=owner['response_sha256']
        or any(records[0].get(k) is not True for k in ('protected_intent_verified','protected_completion_verified','result_hash_verified'))
        or records[0].get('source_table_access') is not False or records[0].get('database_writes')!=0 or records[0].get('messages_sent')!=0):
        raise ValueError('Independent protected account-read acceptance unavailable')
    result=dict(execution=args.execution,status='passed',record=records[0],configuration_verified=True,job_settings_unchanged=True,dispatch=dispatch)
    result['protected_receipt']=retain(dict(owner=owner,result=result),'completed')
    (FOLDER/'read-verification-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__': main()
