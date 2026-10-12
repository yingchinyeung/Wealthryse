"""Allow one explicitly reviewed retry only after protected pre-SQL absence.

Original intent is retained. This gate accepts only the exact module-file
metadata fix and policy-dependency deduplication, never a changed SQL migration,
wrapper, role/database scope, service grant, image or job configuration.
"""
import base64
import gzip
import json


FAILED_EXECUTION = 'ibkrv2stg-bootstrap-8jrej7c'


def payload(template):
    args = template['containers'][0]['args']
    if args[0] != '-c':
        raise ValueError('Unregistered original payload launcher')
    encoded = args[1].split("base64.b64decode('",1)[1].split("'",1)[0]
    return gzip.decompress(base64.b64decode(encoded)).decode()


def validate_payload_change(original,corrected):
    expected = original
    for name in ('util','db','owner_codex_tasks'):
        before = "m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\n"
        after = before.rstrip('\n')+";m.__file__='/app/src/ibkr_etl/"+name+".py'\n"
        if expected.count(before) != 1:
            raise ValueError('Original module initialization differs')
        expected = expected.replace(before,after)
    before = "h=types.ModuleType('owner_task_sql_acceptance');sys.modules[h.__name__]=h\n"
    after = "h=types.ModuleType('owner_task_sql_acceptance');h.__file__='/app/scripts/verify_owner_task_postgres.py';sys.modules[h.__name__]=h\n"
    if expected.count(before) != 1 or expected.count('SELECT c.relname,nf.nspname AS function_schema,fp.proname') != 1:
        raise ValueError('Original harness initialization or dependency query differs')
    expected = expected.replace(before,after).replace('SELECT c.relname,nf.nspname AS function_schema,fp.proname',
        'SELECT DISTINCT c.relname,nf.nspname AS function_schema,fp.proname')
    if corrected != expected:
        raise ValueError('Retry changes exceed the reviewed packaging/query corrections')


def select_retry(deploy,original_folder):
    original_raw = json.loads((original_folder/'run-intent.json').read_text())
    original = deploy.protected(original_raw,'run-intent')
    dispatch = deploy.protected(json.loads((original_folder/'run-dispatch.json').read_text()),'run-dispatch')
    failure_raw = json.loads((original_folder/'startup-reconciliation.json').read_text())
    failure = deploy.protected(failure_raw,'startup-reconciled')
    scope = deploy.protected(json.loads((original_folder/'scope-acceptance.json').read_text()),'scope-accepted')
    required = dict(status='passed',trial_database_absent=True,trial_role_absent=True,
        production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,
        existing_service_grants_changed=False,model_calls=0)
    if (dispatch['execution'] != FAILED_EXECUTION or failure['execution'] != FAILED_EXECUTION
            or failure['source'] != original['source'] or dispatch['source'] != original['source']
            or dispatch['intent_receipt'] != original_raw['protected_receipt']
            or failure['intent_receipt'] != original_raw['protected_receipt']
            or failure['failure_before_sql_wrapper'] is not True or failure['automatic_retry'] is not False
            or scope['status'] != 'passed' or scope['record'] != required
            or scope['failure_receipt'] != failure_raw['protected_receipt']
            or scope['baseline_unchanged'] is not True or scope['source']['files'] != original['source']['files']):
        raise ValueError('Protected pre-SQL failure and live exact object absence required')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted,current = deploy.template(job,'run')
    allowed_changes = {'scripts/deploy_owner_task_postgres_trial.py','scripts/verify_owner_task_postgres.py'}
    if any(current['files'].get(name) != digest for name,digest in original['source']['files'].items()
            if name not in allowed_changes):
        raise ValueError('Frozen migration/wrapper/authority/archive sources changed')
    validate_payload_change(payload(original['template']),payload(wanted))
    if deploy.settings(job) != original['bootstrap_settings_sha256'] or deploy.baseline() != original['baseline']:
        raise ValueError('Original application and job baseline changed before retry')
    return original_folder.with_name(original_folder.name+'-retry1')
