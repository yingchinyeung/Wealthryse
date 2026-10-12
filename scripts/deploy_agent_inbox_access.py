"""Protected manual-only candidate checks and exact website reader provisioning.

Does not change website traffic, identity, auth settings, report publication,
notification rules, existing jobs, source rows or schedules. Each dispatch has
one protected intent and is never silently retried.
"""
import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

from audit_azure_runtime import az
from deploy_metadata_monitor import request,BASE,SUB,VAULT
from deploy_specialist_report_worker import ROOT
from run_specialist_gateway_read_verification import retain

FOLDER=ROOT/'.work/specialist-inbox-access-20261003'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4'
PREFIX='SPECIALIST_INBOX_ACCESS '
JOBS={'inspect':'ibkrv2stg-inbox-acceptance','test':'ibkrv2stg-inbox-acceptance','grant':'ibkrv2stg-inbox-reader-grant'}


def payload(stage):
    if stage not in JOBS: raise ValueError('Unregistered stage')
    if stage=='grant':
        source='''import os,json,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row,options='-c statement_timeout=30000 -c lock_timeout=5000') as c:
    if c.info.host!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or c.info.dbname!='ibkr_staging': raise ValueError('Unexpected grant target')
    with c.transaction():
        role=c.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname='ibkr_stg_web'").fetchone()
        if not role or any(role.values()): raise ValueError('Unexpected web role capabilities')
        for table in ('specialist_daily_reports','specialist_report_deliveries'):
            row=c.execute("SELECT relrowsecurity,relforcerowsecurity,has_table_privilege('ibkr_stg_web',oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS mutable FROM pg_class WHERE oid=%s::regclass",('public.'+table,)).fetchone()
            if not row or not row['relrowsecurity'] or not row['relforcerowsecurity'] or row['mutable']: raise ValueError('Forced-RLS read-only table required')
        if c.execute("SELECT has_table_privilege('ibkr_stg_web','public.specialist_audit_events','SELECT') AS allowed").fetchone()['allowed']: raise ValueError('Unexpected audit access')
        c.execute('GRANT SELECT ON public.specialist_daily_reports,public.specialist_report_deliveries TO ibkr_stg_web')
print('SPECIALIST_INBOX_ACCESS '+json.dumps(dict(status='passed',mode='grant',role='ibkr_stg_web',grants=['SELECT public.specialist_daily_reports','SELECT public.specialist_report_deliveries'],role_memberships_changed=False,source_rows_changed=0,emails_sent=0)),flush=True)
'''
    else: source=(ROOT/'scripts/check_agent_inbox_access.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(source.encode()).decode()
    return ['-c',"import base64;exec(compile(base64.b64decode('"+encoded+"'),'private-inbox-acceptance','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def definition(stage):
    name=JOBS[stage]
    identity=BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-'+('ingestion' if stage=='grant' else 'web')+'-identity'
    secret='database-admin-url' if stage=='grant' else 'database-web-url'
    args,digest=payload(stage)
    env=[dict(name='DATABASE_URL',secretRef='database-url')]
    if stage!='grant': env.extend([dict(name='PORTFOLIO_TENANT_ID',value='00000000-0000-0000-0000-000000000001'),
        dict(name='AUTHORIZATION_MODE',value='database'),dict(name='INBOX_ACCEPTANCE_MODE',value=stage)])
    return dict(location='eastasia',identity=dict(type='UserAssigned',userAssignedIdentities={identity:{}}),
        tags=dict(purpose='specialist-inbox-acceptance',mode='manual-only',payloadSha256=digest),properties=dict(
        environmentId=BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env',configuration=dict(triggerType='Manual',
        replicaTimeout=180,replicaRetryLimit=0,manualTriggerConfig=dict(parallelism=1,replicaCompletionCount=1),
        secrets=[dict(name='database-url',keyVaultUrl='https://'+VAULT+'.vault.azure.net/secrets/'+secret,identity=identity)],
        registries=[dict(server='ibkrv2stgauhecwc4itaoo.azurecr.io',identity=identity)]),template=dict(containers=[
        dict(name=name,image=IMAGE,command=['python'],args=args,env=env,resources=dict(cpu=0.5,memory='1Gi'))])))


def job_path(stage): return BASE+'Microsoft.App/jobs/'+JOBS[stage]


def fingerprint(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_job(value,wanted):
    if {k.lower() for k in value['identity']['userAssignedIdentities']}!={k.lower() for k in wanted['identity']['userAssignedIdentities']}:
        raise ValueError('Acceptance identity differs')
    a=value['properties']; b=wanted['properties']
    for k in ('triggerType','replicaTimeout','replicaRetryLimit','manualTriggerConfig','secrets','registries'):
        if a['configuration'].get(k)!=b['configuration'][k]:
            # Azure adds passwordSecretRef/server fields but must retain reviewed bindings.
            if k not in ('secrets','registries'): raise ValueError('Acceptance controls differ')
            keys=('name','keyVaultUrl','identity') if k=='secrets' else ('server','identity')
            if [{key:r.get(key) for key in keys} for r in a['configuration'].get(k,[])]!=[{key:r.get(key) for key in keys} for r in b['configuration'][k]]:
                raise ValueError('Acceptance credential or registry differs')
    containers=a['template']['containers']; expected=b['template']['containers'][0]
    if len(containers)!=1 or any(containers[0].get(k)!=expected[k] for k in ('name','image','command','args','env')):
        raise ValueError('Acceptance runtime differs')
    resources=containers[0].get('resources',{})
    if any(resources.get(k)!=v for k,v in expected['resources'].items()) or resources.get('ephemeralStorage') not in (None,'') or set(resources)-{'cpu','memory','ephemeralStorage'}:
        raise ValueError('Acceptance resource limits differ')
    if a['template'].get('volumes'): raise ValueError('Unexpected acceptance volume')


def main():
    global FOLDER,JOBS
    p=argparse.ArgumentParser();p.add_argument('stage',choices=tuple(JOBS));p.add_argument('action',choices=('start','resume-prepared','inspect','record-failed','verify'));p.add_argument('--execution');p.add_argument('--attempt',type=int,default=2);args=p.parse_args()
    if args.attempt!=2: raise ValueError('Only the reviewed corrected second attempt is supported')
    FOLDER=ROOT/'.work/specialist-inbox-access-20261003-r2'
    JOBS={key:value+'-r2' for key,value in JOBS.items()}
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    candidate=json.loads((ROOT/'.work/ai-inbox-candidate-20261003-r2/acceptance.json').read_text())
    if candidate.get('status')!='passed' or candidate.get('image')!=IMAGE or not candidate['protected_receipt']['protected_readback_verified']:
        raise ValueError('Verified immutable candidate required')
    wanted=definition(args.stage); path=job_path(args.stage)+'?api-version=2024-03-01'
    FOLDER.mkdir(parents=True,exist_ok=True)
    intent=FOLDER/(args.stage+'-intent.json'); dispatch=FOLDER/(args.stage+'-dispatch.json')
    if args.action in ('start','resume-prepared'):
        resuming=args.action=='resume-prepared'
        if dispatch.exists() or (intent.exists() and not resuming): raise ValueError('Dispatch already registered; inspect instead of retrying')
        if args.stage in ('grant','test'):
            prior=json.loads((FOLDER/(('inspect' if args.stage=='grant' else 'grant')+'-acceptance.json')).read_text())
            if prior['status']!='passed': raise ValueError('Prior acceptance required')
        existing=request('GET',path,missing=True)
        if existing:
            if args.stage!='test' and not resuming: raise ValueError('Acceptance job exists; inspect instead of replacement')
            validate_job(existing,definition('inspect') if args.stage=='test' else wanted)
            runs=request('GET',job_path(args.stage)+'/executions?api-version=2024-03-01').get('value',[])
            if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Acceptance job busy')
            if resuming and runs: raise ValueError('Unknown or prior execution; inspect before dispatch')
        if resuming:
            saved=json.loads(intent.read_text())
            if not existing or saved['definition']!=wanted or not saved['protected_receipt']['protected_readback_verified']:
                raise ValueError('Prepared intent or resource differs')
            receipt=saved['protected_receipt']
        else:
            receipt=retain(dict(stage=args.stage,definition=wanted,source_rows_changed=0,emails_sent=0),'inbox-'+args.stage+'-intent')
            intent.write_text(json.dumps(dict(definition=wanted,protected_receipt=receipt),indent=2),encoding='utf-8')
        if not existing: request('PUT',path,data=wanted)
        current=request('GET',path);validate_job(current,definition('inspect') if args.stage=='test' else wanted)
        run=request('POST',job_path(args.stage)+'/start?api-version=2024-03-01',data=wanted['properties']['template'])
        record=dict(execution=run['name'],payload_sha256=payload(args.stage)[1],protected_intent=receipt)
        dispatch.write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record),flush=True);return
    record=json.loads(dispatch.read_text())
    if args.execution!=record['execution'] or not re.fullmatch(re.escape(JOBS[args.stage])+'-[a-z0-9]{7}',args.execution or ''):
        raise ValueError('Exact registered execution required')
    if payload(args.stage)[1]!=record['payload_sha256']: raise ValueError('Acceptance source changed')
    current=request('GET',path);validate_job(current,definition('inspect') if args.stage=='test' else wanted)
    run=request('GET',job_path(args.stage)+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual=run['properties']['template']['containers'];expected=wanted['properties']['template']['containers'][0]
    if len(actual)!=1 or any(actual[0].get(k)!=expected[k] for k in ('image','command','args')):
        raise ValueError('Executed candidate differs')
    resources=actual[0].get('resources',{})
    if any(resources.get(k)!=v for k,v in expected['resources'].items()) or resources.get('ephemeralStorage') not in (None,'') or set(resources)-{'cpu','memory','ephemeralStorage'}:
        raise ValueError('Executed resource limits differ')
    env=actual[0].get('env',[])
    if len(env)!=len(expected['env']): raise ValueError('Executed environment differs')
    for a,b in zip(env,expected['env']):
        if a.get('name')!=b['name'] or ('value' in b and a.get('value')!=b['value']) or ('secretRef' in b and (a.get('secretRef') not in (b['secretRef'],'cappjob-'+JOBS[args.stage]) or 'value' in a)):
            raise ValueError('Executed credential differs')
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.action=='inspect': print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)),flush=True);return
    if args.action=='record-failed':
        if run['properties']['status']!='Failed' or len(records)!=1 or records[0].get('status')!='failed':
            raise ValueError('Verified failure evidence required')
        result=dict(status='failed',execution=args.execution,stage=args.stage,image=IMAGE,record=records[0],
            website_changed=False,notifications_enabled=False,source_rows_changed=0,emails_sent=0)
        result['protected_receipt']=retain(result,'inbox-'+args.stage+'-failed')
        (FOLDER/(args.stage+'-failure.json')).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True);return
    if run['properties']['status']!='Succeeded' or len(records)!=1 or records[0].get('status')!='passed' or records[0].get('source_rows_changed')!=0 or records[0].get('emails_sent')!=0:
        raise ValueError('Successful restricted acceptance required')
    result=dict(status='passed',execution=args.execution,stage=args.stage,image=IMAGE,record=records[0],website_changed=False,notifications_enabled=False)
    result['protected_receipt']=retain(result,'inbox-'+args.stage+'-completed')
    (FOLDER/(args.stage+'-acceptance.json')).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)


if __name__=='__main__': main()
