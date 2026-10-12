"""Protected metadata-only diagnosis of the exact failed disposable SQL trial.

No retries, source/application rows, DDL, grants, role changes or model calls.
Role/database names are fixed. Admin connection stays inside trusted wrapper.
"""
import argparse
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

import deploy_owner_task_postgres_trial as deploy

FOLDER = deploy.ORIGINAL_FOLDER.with_name(deploy.ORIGINAL_FOLDER.name+'-retry1')
FAILED_EXECUTION = 'ibkrv2stg-bootstrap-85v8wm8'
PREFIX = 'OWNER_TASK_FAILURE_METADATA '


def template(job):
    wanted,frozen = deploy.template(job,'run')
    wrapper = (deploy.ROOT/'scripts/run_owner_task_postgres_trial.py').read_text(encoding='utf-8')
    code = "import os,json,psycopg\nfrom psycopg.rows import dict_row\n"
    code += "scope={'__name__':'owner_task_failure_metadata'}\nexec(compile("+repr(wrapper)+",'reviewed-trusted-wrapper','exec'),scope)\n"
    code += "params=scope['validate_admin_url'](os.environ['DATABASE_ADMIN_URL'])\n"
    code += """with psycopg.connect(**dict(params,dbname='postgres'),autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=20000 -c default_transaction_read_only=on') as raw:
 if raw.info.host!=scope['HOST'] or raw.info.dbname!='postgres': raise ValueError('Unexpected metadata target')
 database,role=scope['metadata'](raw)
 connector=raw.execute('SELECT current_user AS name').fetchone()['name']
 member_rows=raw.execute('SELECT pg_get_userbyid(member) AS member,pg_get_userbyid(grantor) AS grantor,grantor=10 AS bootstrap_grantor,admin_option,inherit_option,set_option FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY grantor',(scope['ROLE'],)).fetchall()
 acl_rows=raw.execute("SELECT pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type,a.is_grantable FROM pg_database d,LATERAL aclexplode(d.datacl) a WHERE d.datname=%s ORDER BY a.grantee,a.privilege_type",(scope['DATABASE'],)).fetchall()
 result=dict(status='observed',trial_database_exists=database is not None,trial_role_exists=role is not None,database_marker_matches=bool(database and database['marker']==scope['MARKER']),database_owner_matches=bool(database and database['owner']==scope['ROLE']),role_marker_matches=bool(role and role['marker']==scope['MARKER']),role_attributes={k:role[k] for k in ('rolcanlogin','rolinherit','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')} if role else None,connector_attributes=raw.execute('SELECT rolinherit,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone(),creator_self_grant=raw.execute("SELECT current_setting('createrole_self_grant',true) AS value").fetchone()['value'],memberships=[dict(member_is_admin=row['member']==connector,member_is_azure_admin=row['member']=='azure_pg_admin',grantor_is_admin=row['grantor']==connector,grantor_is_bootstrap=row['bootstrap_grantor'],admin_option=row['admin_option'],inherit_option=row['inherit_option'],set_option=row['set_option']) for row in member_rows],database_acl=[dict(recipient='trial-owner' if row['grantee']==scope['ROLE'] else 'bootstrap-connector' if row['grantee']==connector else 'PUBLIC' if row['grantee']=='PUBLIC' else 'other',grantor_is_trial_owner=row['grantor']==scope['ROLE'],privilege=row['privilege_type'],grantable=row['is_grantable']) for row in acl_rows],outgoing_memberships=raw.execute('SELECT count(*) AS n FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)',(scope['ROLE'],)).fetchone()['n'],production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,existing_service_grants_changed=False,model_calls=0)
 if database and database['marker']==scope['MARKER'] and database['owner']==scope['ROLE']:
  with psycopg.connect(**dict(params,dbname=scope['DATABASE']),autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=20000 -c default_transaction_read_only=on') as trial:
   if trial.info.host!=scope['HOST'] or trial.info.dbname!=scope['DATABASE']: raise ValueError('Unexpected disposable metadata target')
   schemas=trial.execute("SELECT nspname,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY nspname").fetchall()
   result['schema_inventory']=[dict(registered=row['nspname'] in (scope['SCHEMA'],'public'),trial_schema=row['nspname']==scope['SCHEMA'],trial_owner=row['owner']==scope['ROLE']) for row in schemas]
   result['permanent_relation_count']=trial.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()['n']
   result['task_function_count']=trial.execute('SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s',(scope['SCHEMA'],)).fetchone()['n']
 print('"""+PREFIX+"""'+json.dumps(result,sort_keys=True),flush=True)
"""
    encoded = base64.b64encode(gzip.compress(code.encode(),mtime=0)).decode()
    wanted['containers'][0]['args'] = ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'fixed-disposable-metadata-diagnosis','exec'))"]
    wanted['containers'][0]['env'][1]['value'] = 'metadata-diagnosis'
    if deploy.bundle('run')[1] != frozen:
        raise ValueError('Frozen source changed')
    frozen['metadata_probe_sha256'] = hashlib.sha256(code.encode()).hexdigest()
    frozen['metadata_dispatcher_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return wanted,frozen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['start','inspect','verify'])
    parser.add_argument('--execution')
    args = parser.parse_args()
    deploy.FOLDER = FOLDER
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    original_raw = json.loads((FOLDER/'run-intent.json').read_text())
    original = deploy.protected(original_raw,'run-intent')
    dispatch = deploy.protected(json.loads((FOLDER/'run-dispatch.json').read_text()),'run-dispatch')
    if dispatch['execution'] != FAILED_EXECUTION or dispatch['intent_receipt'] != original_raw['protected_receipt']:
        raise ValueError('Exact failed trial binding required')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted,source = template(job)
    if source['files'] != original['source']['files'] or deploy.settings(job) != original['bootstrap_settings_sha256']:
        raise ValueError('Failed trial source/settings changed')
    if args.stage == 'start':
        if (FOLDER/'metadata-intent.json').exists() or (FOLDER/'metadata-dispatch.json').exists():
            raise ValueError('Metadata intent exists; no redispatch')
        failed = deploy.request('GET',deploy.PATH+'/executions/'+FAILED_EXECUTION+'?api-version=2024-03-01')
        if failed['properties']['status'] != 'Failed' or deploy.baseline() != original['baseline']:
            raise ValueError('Terminal failed trial and unchanged baseline required')
        deploy.verify_containers(failed['properties']['template']['containers'],original['template'])
        runs = deploy.request('GET',deploy.PATH+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs):
            raise ValueError('Bootstrap busy or unknown')
        intent = deploy.save(dict(stage='metadata-diagnosis',source=source,template=wanted,
            failed_trial=FAILED_EXECUTION,failed_trial_intent=original_raw['protected_receipt'],
            baseline=original['baseline'],read_only=True,automatic_retry=False),'metadata-intent.json','metadata-intent')
        deploy.protected(intent,'metadata-intent')
        run = deploy.request('POST',deploy.PATH+'/start?api-version=2024-03-01',data=wanted)
        saved = deploy.save(dict(execution=run['name'],source=source,intent_receipt=intent['protected_receipt']),
            'metadata-dispatch.json','metadata-dispatch')
        print(json.dumps(dict(execution=saved['execution'],read_only=True,protected_intent_verified=True)),flush=True)
        return
    intent_raw = json.loads((FOLDER/'metadata-intent.json').read_text())
    intent = deploy.protected(intent_raw,'metadata-intent')
    dispatch = deploy.protected(json.loads((FOLDER/'metadata-dispatch.json').read_text()),'metadata-dispatch')
    if (args.execution != dispatch['execution'] or not re.fullmatch(deploy.JOB+r'-[a-z0-9]{7}',args.execution or '')
            or intent['source'] != source or dispatch['source'] != source
            or dispatch['intent_receipt'] != intent_raw['protected_receipt']):
        raise ValueError('Exact metadata execution/source required')
    run = deploy.request('GET',deploy.PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual = run['properties']['template']
    if actual.get('volumes') or actual.get('initContainers'):
        raise ValueError('Unregistered metadata extension')
    deploy.verify_containers(actual['containers'],wanted)
    rows = deploy.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
        "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records = [json.loads(row['Log_s'].removeprefix(PREFIX)) for row in rows]
    if args.stage == 'inspect':
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)),flush=True)
        return
    if (run['properties']['status'] != 'Succeeded' or len(records) != 1 or records[0].get('status') != 'observed'
            or any(records[0].get(name) is not False for name in ('production_database_touched','ibkr_source_writes',
                'ddl_executed','existing_service_grants_changed')) or records[0].get('model_calls') != 0):
        raise ValueError('Read-only metadata record required; no retry')
    if deploy.baseline() != intent['baseline'] or template(job)[1] != source:
        raise ValueError('Baseline or metadata source changed')
    saved = deploy.save(dict(status='observed',execution=args.execution,source=source,record=records[0],
        failed_trial=FAILED_EXECUTION,baseline_unchanged=True,existing_jobs_unchanged=34),
        'metadata-evidence.json','metadata-observed')
    print(json.dumps(saved,sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
