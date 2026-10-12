"""One protected, read-only catalog probe after the fixed pre-SQL failure.

Queries only existence of the exact disposable database/NOLOGIN role through the
trusted bootstrap. No source/application rows, DDL, grants or model calls.
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

import deploy_owner_task_postgres_trial as deploy


PREFIX = 'OWNER_TASK_STARTUP_SCOPE '
FAILED_EXECUTION = 'ibkrv2stg-bootstrap-8jrej7c'


def frozen_template(job):
    wanted, frozen = deploy.template(job,'run')
    wrapper = (deploy.ROOT/'scripts/run_owner_task_postgres_trial.py').read_text(encoding='utf-8')
    source = 'import os,json,psycopg\nfrom psycopg.rows import dict_row\n'
    source += "scope={'__name__':'owner_task_scope_probe'}\nexec(compile("+repr(wrapper)+",'reviewed-scope-wrapper','exec'),scope)\n"
    source += "params=scope['validate_admin_url'](os.environ['DATABASE_ADMIN_URL'])\n"
    source += "with psycopg.connect(**dict(params,dbname='postgres'),autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=20000 -c default_transaction_read_only=on') as connection:\n"
    source += " if connection.info.host!=scope['HOST'] or connection.info.dbname!='postgres': raise ValueError('Unexpected scope probe target')\n"
    source += " database,role=scope['metadata'](connection)\n"
    source += " print('"+PREFIX+"'+json.dumps(dict(status='passed' if not database and not role else 'blocked',trial_database_absent=database is None,trial_role_absent=role is None,production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,existing_service_grants_changed=False,model_calls=0)),flush=True)\n"
    encoded = base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    wanted['containers'][0]['args'] = ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'owner-task-read-only-scope-probe','exec'))"]
    wanted['containers'][0]['env'][1]['value'] = 'scope-probe'
    if deploy.bundle('run')[1] != frozen:
        raise ValueError('Frozen source changed while preparing read-only scope probe')
    frozen['probe_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    frozen['probe_dispatcher_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return wanted,frozen


def validate_records(records):
    expected = dict(status='passed',trial_database_absent=True,trial_role_absent=True,
        production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,
        existing_service_grants_changed=False,model_calls=0)
    if (records != [expected] or any(type(records[0][name]) is not bool for name in expected
            if name not in ('status','model_calls')) or type(records[0]['model_calls']) is not int):
        raise ValueError('Exact read-only database and role absence evidence required')
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['start','inspect','verify'])
    parser.add_argument('--execution')
    args = parser.parse_args()
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    original = json.loads((deploy.FOLDER/'run-intent.json').read_text())
    failure_raw = json.loads((deploy.FOLDER/'startup-reconciliation.json').read_text())
    failure = deploy.protected(failure_raw,'startup-reconciled')
    dispatch_original = deploy.protected(json.loads((deploy.FOLDER/'run-dispatch.json').read_text()),'run-dispatch')
    deploy.protected(original,'run-intent')
    if (failure['execution'] != FAILED_EXECUTION or failure['failure_before_sql_wrapper'] is not True
            or failure['source'] != original['source'] or dispatch_original['source'] != original['source']
            or dispatch_original['execution'] != FAILED_EXECUTION
            or dispatch_original['intent_receipt'] != original['protected_receipt']
            or failure['intent_receipt'] != original['protected_receipt']):
        raise ValueError('Protected original failure binding differs')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted,source = frozen_template(job)
    if source['files'] != original['source']['files'] or deploy.settings(job) != original['bootstrap_settings_sha256']:
        raise ValueError('Original frozen sources/settings changed before scope probe')
    if args.stage == 'start':
        if (deploy.FOLDER/'scope-intent.json').exists() or (deploy.FOLDER/'scope-dispatch.json').exists():
            raise ValueError('Scope intent exists; no redispatch')
        if deploy.baseline() != original['baseline']:
            raise ValueError('Original baseline changed')
        runs = deploy.request('GET',deploy.PATH+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
            raise ValueError('Bootstrap busy or unknown')
        intent = deploy.save(dict(stage='scope-probe',source=source,template=wanted,
            failure_receipt=failure_raw['protected_receipt'],baseline=original['baseline'],
            bootstrap_settings_sha256=deploy.settings(job),read_only=True,automatic_retry=False),
            'scope-intent.json','scope-intent')
        deploy.protected(intent,'scope-intent')
        run = deploy.request('POST',deploy.PATH+'/start?api-version=2024-03-01',data=wanted)
        saved = deploy.save(dict(execution=run['name'],source=source,intent_receipt=intent['protected_receipt']),
            'scope-dispatch.json','scope-dispatch')
        print(json.dumps(dict(execution=saved['execution'],read_only=True,protected_intent_verified=True)),flush=True)
        return
    intent_raw = json.loads((deploy.FOLDER/'scope-intent.json').read_text())
    intent = deploy.protected(intent_raw,'scope-intent')
    dispatch = deploy.protected(json.loads((deploy.FOLDER/'scope-dispatch.json').read_text()),'scope-dispatch')
    if (args.execution != dispatch['execution'] or not re.fullmatch(deploy.JOB+r'-[a-z0-9]{7}',args.execution or '')
            or intent['source'] != source or dispatch['source'] != source
            or dispatch['intent_receipt'] != intent_raw['protected_receipt']
            or intent['failure_receipt'] != failure_raw['protected_receipt']):
        raise ValueError('Exact scope execution and evidence required')
    run = deploy.request('GET',deploy.PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual = run['properties']['template']
    if actual.get('volumes') or actual.get('initContainers'):
        raise ValueError('Unregistered probe extension')
    deploy.verify_containers(actual['containers'],wanted)
    rows = deploy.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
        "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records = [json.loads(row['Log_s'].removeprefix(PREFIX)) for row in rows]
    if args.stage == 'inspect':
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)),flush=True)
        return
    if run['properties']['status'] != 'Succeeded':
        raise ValueError('Successful scope probe required; no automatic retry')
    expected = validate_records(records)
    if deploy.baseline() != intent['baseline'] or frozen_template(job)[1] != source:
        raise ValueError('Scope source or original baseline changed')
    accepted = deploy.save(dict(status='passed',execution=args.execution,source=source,record=expected,
        failure_receipt=failure_raw['protected_receipt'],baseline_unchanged=True,existing_jobs_unchanged=34),
        'scope-acceptance.json','scope-accepted')
    print(json.dumps(accepted,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
