"""Remove only the reviewed empty disposable database and NOLOGIN trial role.

Exact protected metadata is read again before deletion. No FORCE, session
termination, production changes, grant changes or automatic retries.
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

import inspect_owner_task_trial_failure as diagnostic
import deploy_owner_task_postgres_trial as deploy

FOLDER = diagnostic.FOLDER
PREFIX = 'OWNER_TASK_FAILED_SETUP_CLEANUP '


def validate_empty_metadata(record):
    if (record.get('status') != 'observed'
            or any(record.get(k) is not True for k in ('trial_database_exists','trial_role_exists',
                'database_marker_matches','database_owner_matches','role_marker_matches'))
            or any(record.get(k) is not False for k in ('production_database_touched','ibkr_source_writes',
                'ddl_executed','existing_service_grants_changed'))
            or record.get('model_calls') != 0 or record.get('outgoing_memberships') != 0
            or record.get('permanent_relation_count') != 0 or record.get('task_function_count') != 0
            or record.get('role_attributes') != dict.fromkeys(('rolcanlogin','rolinherit','rolsuper',
                'rolcreatedb','rolcreaterole','rolreplication','rolbypassrls'),False)
            or record.get('schema_inventory') != [dict(registered=True,trial_schema=True,trial_owner=True),
                dict(registered=True,trial_schema=False,trial_owner=False)]):
        raise ValueError('Exact empty marked disposable scope required')
    expected = [dict(member_is_admin=True,member_is_azure_admin=False,grantor_is_admin=False,
        grantor_is_bootstrap=True,admin_option=True,inherit_option=False,set_option=False),
        dict(member_is_admin=True,member_is_azure_admin=False,grantor_is_admin=True,
        grantor_is_bootstrap=False,admin_option=False,inherit_option=True,set_option=True)]
    if record.get('memberships') != expected:
        raise ValueError('Only the reviewed administrator creator/SET grants may exist')
    acl = [dict(recipient='bootstrap-connector',grantor_is_trial_owner=True,privilege='CONNECT',grantable=False)]
    acl += [dict(recipient='trial-owner',grantor_is_trial_owner=True,privilege=k,grantable=False)
        for k in ('CONNECT','CREATE','TEMPORARY')]
    if record.get('database_acl') != acl:
        raise ValueError('Exact disposable database ACL required')


def template(job,metadata):
    validate_empty_metadata(metadata['record'])
    wanted,source = diagnostic.template(job)
    if source != metadata['source']:
        raise ValueError('Frozen diagnostic source changed before cleanup')
    args = wanted['containers'][0]['args']
    encoded = args[1].split("base64.b64decode('",1)[1].split("'",1)[0]
    code = gzip.decompress(base64.b64decode(encoded)).decode()
    before = " print('"+diagnostic.PREFIX+"'+json.dumps(result,sort_keys=True),flush=True)\n"
    if code.count(before) != 1:
        raise ValueError('Exact reviewed metadata runner required')
    after = " if result!="+repr(metadata['record'])+": raise ValueError('Disposable metadata changed; cleanup stopped')\n"
    # LIKE 'pg_%' would treat '_' as a wildcard and omit user schemas such as
    # pgfoo. Prove exact emptiness including sequences, indexes and functions.
    after += " with psycopg.connect(**dict(params,dbname=scope['DATABASE']),autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=20000 -c default_transaction_read_only=on') as empty:\n"
    after += "  if empty.info.host!=scope['HOST'] or empty.info.dbname!=scope['DATABASE']: raise ValueError('Unexpected empty-scope target')\n"
    after += "  schemas=empty.execute(\"SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema' ORDER BY nspname\").fetchall()\n"
    after += "  if schemas!=[{'nspname':scope['SCHEMA']},{'nspname':'public'}]: raise ValueError('Unexpected disposable user namespace; cleanup stopped')\n"
    after += "  if empty.execute(\"SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'\").fetchone()['n']: raise ValueError('Disposable relations or sequences exist; cleanup stopped')\n"
    after += "  if empty.execute(\"SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'\").fetchone()['n']: raise ValueError('Disposable user functions exist; cleanup stopped')\n"
    after += " if raw.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE datname=%s',(scope['DATABASE'],)).fetchone()['n']: raise ValueError('Active disposable connection; cleanup stopped')\n"
    after += " raw.execute('SET default_transaction_read_only=off')\n"
    after += " raw.execute(scope['sql'].SQL('SET ROLE {}').format(scope['sql'].Identifier(scope['ROLE'])))\n"
    after += " try:\n  raw.execute(scope['sql'].SQL('DROP DATABASE {}').format(scope['sql'].Identifier(scope['DATABASE'])))\n finally:\n  raw.execute('RESET ROLE')\n"
    after += " raw.execute(scope['sql'].SQL('DROP ROLE {}').format(scope['sql'].Identifier(scope['ROLE'])))\n"
    after += " if any(scope['metadata'](raw)): raise ValueError('Disposable absence readback failed')\n"
    after += " print('"+PREFIX+"'+json.dumps(dict(status='passed',stage='failed-setup-cleanup',trial_database_removed=True,trial_role_removed=True,production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False,model_calls=0)),flush=True)\n"
    code = code.replace(before,after)
    encoded = base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
    wanted['containers'][0]['args'] = ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'reviewed-empty-disposable-cleanup','exec'))"]
    wanted['containers'][0]['env'][1]['value'] = 'failed-setup-cleanup'
    source = dict(source,cleanup_payload_sha256=hashlib.sha256(code.encode()).hexdigest(),
        cleanup_dispatcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    return wanted,source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['start','inspect','verify'])
    parser.add_argument('--execution')
    args = parser.parse_args()
    deploy.FOLDER = FOLDER
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    metadata_raw = json.loads((FOLDER/'metadata-evidence.json').read_text())
    metadata = deploy.protected(metadata_raw,'metadata-observed')
    if metadata['failed_trial'] != diagnostic.FAILED_EXECUTION or metadata['baseline_unchanged'] is not True:
        raise ValueError('Exact protected failed-setup diagnosis required')
    original = deploy.protected(json.loads((FOLDER/'run-intent.json').read_text()),'run-intent')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted,source = template(job,metadata)
    if (source['files'] != original['source']['files']
            or deploy.settings(job) != original['bootstrap_settings_sha256']):
        raise ValueError('Failed trial source/settings changed')
    if args.stage == 'start':
        if (FOLDER/'failed-cleanup-intent.json').exists() or (FOLDER/'failed-cleanup-dispatch.json').exists():
            raise ValueError('Cleanup intent exists; no redispatch')
        if deploy.baseline() != original['baseline']:
            raise ValueError('Original baseline changed')
        runs = deploy.request('GET',deploy.PATH+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
            raise ValueError('Bootstrap busy or unknown')
        intent = deploy.save(dict(stage='failed-setup-cleanup',source=source,template=wanted,
            metadata_receipt=metadata_raw['protected_receipt'],baseline=original['baseline'],
            automatic_retry=False,production_database_touched=False),
            'failed-cleanup-intent.json','failed-cleanup-intent')
        deploy.protected(intent,'failed-cleanup-intent')
        run = deploy.request('POST',deploy.PATH+'/start?api-version=2024-03-01',data=wanted)
        saved = deploy.save(dict(execution=run['name'],source=source,intent_receipt=intent['protected_receipt']),
            'failed-cleanup-dispatch.json','failed-cleanup-dispatch')
        print(json.dumps(dict(execution=saved['execution'],protected_intent_verified=True,exact_empty_scope=True)),flush=True)
        return
    intent_raw = json.loads((FOLDER/'failed-cleanup-intent.json').read_text())
    intent = deploy.protected(intent_raw,'failed-cleanup-intent')
    dispatch = deploy.protected(json.loads((FOLDER/'failed-cleanup-dispatch.json').read_text()),'failed-cleanup-dispatch')
    if (args.execution != dispatch['execution'] or not re.fullmatch(deploy.JOB+r'-[a-z0-9]{7}',args.execution or '')
            or intent['source'] != source or dispatch['source'] != source
            or dispatch['intent_receipt'] != intent_raw['protected_receipt']
            or intent['metadata_receipt'] != metadata_raw['protected_receipt']):
        raise ValueError('Exact cleanup execution and protected diagnosis required')
    run = deploy.request('GET',deploy.PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual = run['properties']['template']
    if actual.get('volumes') or actual.get('initContainers'):
        raise ValueError('Unregistered cleanup extension')
    deploy.verify_containers(actual['containers'],wanted)
    rows = deploy.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
        "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records = [json.loads(row['Log_s'].removeprefix(PREFIX)) for row in rows]
    if args.stage == 'inspect':
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)),flush=True)
        return
    expected = dict(status='passed',stage='failed-setup-cleanup',trial_database_removed=True,trial_role_removed=True,
        production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False,model_calls=0)
    if run['properties']['status'] != 'Succeeded' or records != [expected]:
        raise ValueError('Exact disposable cleanup absence readback required; no retry')
    if deploy.baseline() != intent['baseline'] or template(job,metadata)[1] != source:
        raise ValueError('Cleanup source or original baseline changed')
    saved = deploy.save(dict(status='passed',stage='failed-setup-cleanup',execution=args.execution,
        source=source,record=expected,metadata_receipt=metadata_raw['protected_receipt'],
        baseline_unchanged=True,existing_jobs_unchanged=34),
        'failed-cleanup-acceptance.json','failed-cleanup-accepted')
    print(json.dumps(saved,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
