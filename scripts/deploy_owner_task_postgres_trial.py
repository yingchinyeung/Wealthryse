"""One protected manual SQL acceptance, then separately reviewed exact cleanup.

Only an execution override of the existing idle bootstrap job is submitted.
No job/image/schedule/secret/RBAC definition is changed; no inference occurs.
An intent prevents automatic redispatch even if a response is lost.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
import sys
from datetime import datetime

from azure.core import MatchConditions
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB, ROOT
from deploy_specialist_daily_schedule import unaffected, validate as validate_daily
from deploy_specialist_report_worker import JOB as DAILY, job_path as daily_path
from run_specialist_daily_publication import prerequisites
from run_specialist_gateway_read_verification import retain
from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER, SCOPE
from ibkr_etl.specialist_audit_service import validate_archive_properties

JOB = 'ibkrv2stg-bootstrap'
PATH = BASE+'Microsoft.App/jobs/'+JOB
FOLDER = ROOT/'.work/owner-task-postgres-20261003'
ORIGINAL_FOLDER = FOLDER
PREFIX = 'OWNER_TASK_POSTGRES_TRIAL '
MODULES = ('util','db','owner_codex_tasks')
FILES = tuple('src/ibkr_etl/'+name+'.py' for name in MODULES) + (
    'migrations/postgres/059_owner_codex_tasks.sql','migrations/postgres/001_init.sql',
    'scripts/verify_owner_task_postgres.py','scripts/run_owner_task_postgres_trial.py',
    'scripts/deploy_owner_task_postgres_trial.py',
    'scripts/owner_task_postgres_retry_guard.py',
    'scripts/run_specialist_gateway_read_verification.py',
    'src/ibkr_etl/specialist_archive_reader.py','src/ibkr_etl/specialist_audit_service.py',
    'scripts/deploy_specialist_daily_schedule.py','scripts/run_specialist_daily_publication.py',
    'scripts/deploy_metadata_monitor.py','scripts/audit_azure_runtime.py',
    'scripts/release_verified_image.py','scripts/deploy_specialist_report_worker.py')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def bundle(stage):
    if stage not in ('run','cleanup'):
        raise ValueError('Unregistered trial stage')
    hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}
    source = "import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in MODULES:
        code = (ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source += "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl';m.__file__='/app/src/ibkr_etl/"+name+".py'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'owner-task-trial-module','exec'),m.__dict__)\n"
    harness = (ROOT/'scripts/verify_owner_task_postgres.py').read_text(encoding='utf-8')
    source += "h=types.ModuleType('owner_task_sql_acceptance');h.__file__='/app/scripts/verify_owner_task_postgres.py';sys.modules[h.__name__]=h\nexec(compile("+repr(harness)+",'owner-task-sql-acceptance','exec'),h.__dict__)\n"
    source += 'h.MIGRATION_SQL='+repr((ROOT/'migrations/postgres/059_owner_codex_tasks.sql').read_bytes().decode('utf-8'))+'\n'
    source += 'h.MIGRATION_SHA256='+repr(hashes['migrations/postgres/059_owner_codex_tasks.sql'])+'\n'
    source += 'verify_trial=h.verify_trial\n'
    source += (ROOT/'scripts/run_owner_task_postgres_trial.py').read_text(encoding='utf-8')
    if {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES} != hashes:
        raise ValueError('Trial source changed while freezing the payload')
    encoded = base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    args = ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'owner-task-synthetic-trial','exec'))"]
    return args, dict(files=hashes,payload_sha256=hashlib.sha256(source.encode()).hexdigest())


EXPECTED_CHECKS = [
    'exact_scope_role_catalog_and_migration',
    'forced_tenant_owner_and_missing_scope_rls',
    'immutability_null_checks_and_deferred_event_atomicity',
    'trigger_schema_binding_with_temp_decoys',
    'concurrent_idempotency_payload_conflict_and_single_event',
    'cancel_first_cas_race_sticky_expiry_and_verified_stop_gate',
    'late_cancel_no_redispatch_and_fresh_authority_revocation',
    'committed_final_catalog_readback',
]


def settings(job):
    return digest(dict(identity=job['identity'],configuration=job['properties']['configuration'],
        template=job['properties']['template']))


def validate_bootstrap(job):
    p = job['properties']; config = p['configuration']; containers = p['template']['containers']
    identity = BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity'
    scopes = job.get('identity',{}).get('userAssignedIdentities',{})
    if (job['identity']['type'] != 'UserAssigned' or {x.casefold() for x in scopes} != {identity.casefold()}
            or config['triggerType'] != 'Manual' or config['replicaRetryLimit'] != 0
            or config['manualTriggerConfig'] != {'parallelism':1,'replicaCompletionCount':1}
            or len(containers) != 1 or containers[0]['name'] != 'postgres-bootstrap'
            or containers[0]['image'] != 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
            or p['template'].get('volumes') or p['template'].get('initContainers')
            or containers[0].get('volumeMounts') or containers[0].get('probes')):
        raise ValueError('Existing private bootstrap definition differs')
    secrets = [x for x in config['secrets'] if x['name'] == 'database-admin-url']
    if (len(secrets) != 1 or secrets[0].get('keyVaultUrl') !=
            'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-admin-url'
            or secrets[0].get('identity','').casefold() != identity.casefold()
            or secrets[0].get('value')):
        raise ValueError('Bootstrap administrator secret binding differs')


def template(job,stage):
    validate_bootstrap(job)
    args, record = bundle(stage)
    value = {'containers':[copy.deepcopy(job['properties']['template']['containers'][0])]}
    value['containers'][0].update(command=['python'],args=args,resources={'cpu':0.5,'memory':'1Gi'},
        env=[{'name':'DATABASE_ADMIN_URL','secretRef':'database-admin-url'},
             {'name':'OWNER_TASK_TRIAL_STAGE','value':stage}])
    value['containers'][0].pop('volumeMounts',None)
    return value, record


def archive_protection():
    value = request('GET',SCOPE+'?api-version=2023-05-01')
    if (value.get('id','').casefold() != SCOPE.casefold()
            or value.get('properties',{}).get('hasLegalHold') is not True):
        raise ValueError('Exact protected trial archive identity and hold required')
    validate_archive_properties(value)


def protected(record,kind):
    receipt = record.get('protected_receipt',{})
    value = {k:v for k,v in record.items() if k != 'protected_receipt'}
    raw = json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    sha = hashlib.sha256(raw).hexdigest()
    if (receipt.get('protected_readback_verified') is not True or receipt.get('sha256') != sha
            or receipt.get('blob') != f'acceptance/gateway-read-owner-task-pg-{kind}-{sha}.json'
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z',receipt.get('version_id',''))
            or not 0 < len(raw) <= 2*1024*1024):
        raise ValueError('Exact protected trial receipt required')
    datetime.fromisoformat(receipt['version_id'].replace('Z','+00:00'))
    archive_protection()
    blob = BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client(
        CONTAINER,receipt['blob'],version_id=receipt['version_id'])
    props = blob.get_blob_properties()
    if (props.size != len(raw) or props.version_id != receipt['version_id'] or
            blob.download_blob(offset=0,length=len(raw)+1,etag=props.etag,
                match_condition=MatchConditions.IfNotModified).readall() != raw):
        raise ValueError('Protected trial readback differs')
    archive_protection()
    return value


def save(value,name,kind):
    path = FOLDER/name
    # Exclusive reservation is the single-dispatch gate. An archive/write error
    # deliberately leaves the reservation in place for manual reconciliation.
    with path.open('x',encoding='utf-8') as output:
        archive_protection()
        value = dict(value,protected_receipt=retain(value,'owner-task-pg-'+kind))
        archive_protection()
        output.write(json.dumps(value,sort_keys=True,indent=2))
    return value


def verify_containers(actual,wanted):
    expected = wanted['containers'][0]
    if (len(actual) != 1 or any(actual[0].get(k) != expected[k] for k in ('name','image','command','args'))
            or any(actual[0].get('resources',{}).get(k) != v for k,v in expected['resources'].items())
            or actual[0].get('volumeMounts') or actual[0].get('probes')):
        raise ValueError('Executed trial source/image/capacity differs')
    env = actual[0].get('env',[])
    if (len(env) != 2 or env[0].get('name') != 'DATABASE_ADMIN_URL' or 'value' in env[0]
            or env[0].get('secretRef') not in ('database-admin-url','cappjob-'+JOB)
            or env[1] != expected['env'][1]):
        raise ValueError('Executed trial credential or stage differs')


def baseline():
    prerequisites()
    validate_daily(request('GET',daily_path(DAILY)+'?api-version=2024-03-01'),True)
    return unaffected()


def main():
    global FOLDER
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['start','inspect','verify','cleanup-start','cleanup-inspect','cleanup-verify'])
    parser.add_argument('--execution')
    parser.add_argument('--startup-retry',action='store_true',
        help='One reviewed attempt after protected exact pre-SQL failure and live object absence')
    args = parser.parse_args()
    stage = 'cleanup' if args.stage.startswith('cleanup-') else 'run'
    if az('account','show','--query','id') != SUB:
        raise ValueError('Unexpected subscription')
    if args.startup_retry:
        if args.stage == 'start':
            from owner_task_postgres_retry_guard import select_retry
            FOLDER = select_retry(sys.modules[__name__],ORIGINAL_FOLDER)
        else:
            FOLDER = ORIGINAL_FOLDER.with_name(ORIGINAL_FOLDER.name+'-retry1')
    FOLDER.mkdir(parents=True,exist_ok=True)
    job = request('GET',PATH+'?api-version=2024-03-01')
    wanted, source = template(job,stage)
    ip = FOLDER/(stage+'-intent.json'); dp = FOLDER/(stage+'-dispatch.json')
    if args.stage in ('start','cleanup-start'):
        if ip.exists() or dp.exists():
            raise ValueError('An intent already exists; no automatic redispatch')
        if stage == 'cleanup':
            accepted = protected(json.loads((FOLDER/'run-acceptance.json').read_text()),'run-accepted')
            if accepted.get('status') != 'passed' or accepted['source'] != source:
                raise ValueError('Exact source SQL acceptance required before cleanup')
        baseline_value = baseline()
        runs = request('GET',PATH+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
            raise ValueError('Bootstrap is busy or has an unknown state')
        intent = save(dict(stage=stage,source=source,template=wanted,baseline=baseline_value,
            bootstrap_settings_sha256=settings(job),scope='synthetic-disposable-database-and-NOLOGIN-role',
            startup_retry=args.startup_retry,
            startup_failure_receipt=(json.loads((ORIGINAL_FOLDER/'startup-reconciliation.json').read_text())['protected_receipt']
                if args.startup_retry else None),
            fresh_login_verified=False,production_migration_installed=False,ibkr_source_writes=False,
            existing_service_grants_changed=False,model_calls=0),ip.name,stage+'-intent')
        protected(intent,stage+'-intent')
        run = request('POST',PATH+'/start?api-version=2024-03-01',data=wanted)
        dispatch = save(dict(stage=stage,source=source,execution=run['name'],
            intent_receipt=intent['protected_receipt']),dp.name,stage+'-dispatch')
        print(json.dumps(dict(execution=dispatch['execution'],stage=stage,protected_intent_verified=True)),flush=True)
        return
    intent = protected(json.loads(ip.read_text()),stage+'-intent')
    dispatch = protected(json.loads(dp.read_text()),stage+'-dispatch')
    if (args.execution != dispatch['execution'] or not re.fullmatch(JOB+r'-[a-z0-9]{7}',args.execution or '')
            or dispatch['source'] != source or intent['source'] != source
            or intent.get('startup_retry',False) is not args.startup_retry
            or intent['bootstrap_settings_sha256'] != settings(job)):
        raise ValueError('Frozen exact trial source/job/execution required')
    run = request('GET',PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    executed = run['properties']['template']
    if executed.get('volumes') or executed.get('initContainers'):
        raise ValueError('Executed trial template has an unregistered extension')
    verify_containers(executed['containers'],wanted)
    rows = az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
        "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records = [json.loads(row['Log_s'].removeprefix(PREFIX)) for row in rows]
    if args.stage.endswith('inspect') or args.stage == 'inspect':
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records,
            installed_job_unchanged=True,executed_source_verified=True)),flush=True)
        return
    if run['properties']['status'] != 'Succeeded' or len(records) != 1 or records[0].get('status') != 'passed':
        raise ValueError('Successful SQL acceptance record not yet available; inspect without retry')
    record = records[0]
    if (record.get('stage') != stage or record.get('ibkr_source_writes') is not False
            or record.get('production_database_touched') is not False
            or record.get('existing_service_grants_changed') is not False):
        raise ValueError('SQL acceptance scope differs')
    if stage == 'run' and (record.get('fresh_login_verified') is not False
            or record.get('production_migration_installed') is not False or record.get('checks') != EXPECTED_CHECKS
            or record.get('check_manifest_sha256') != digest(EXPECTED_CHECKS)
            or record.get('migration_sha256') != source['files']['migrations/postgres/059_owner_codex_tasks.sql']):
        raise ValueError('Restricted SQL behavioral acceptance is incomplete')
    if stage == 'cleanup' and (record.get('trial_database_removed') is not True or record.get('trial_role_removed') is not True):
        raise ValueError('Exact disposable cleanup unverified')
    if baseline() != intent['baseline'] or bundle(stage)[1] != source:
        raise ValueError('Application/jobs or test source changed during SQL acceptance')
    accepted = save(dict(status='passed',execution=args.execution,source=source,record=record,
        protected_intent_verified=True,baseline_unchanged=True,existing_jobs_unchanged=34,
        daily_schedule_hkt='18:00',owner_runtime_active=False,six_specialist_runtimes_active=False),
        stage+'-acceptance.json',stage+'-accepted')
    print(json.dumps(accepted,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
