"""Read-only image inspection before any owner-inbox website rollout.

Uses one manual reporting execution override with the exact live web image.
No installed resource, credential, database, source data or schedule is changed.
Only route-presence indicators and file hashes are returned, never file bodies.
"""
import argparse
import base64
import copy
import hashlib
import json
import re

from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB
from deploy_specialist_report_worker import ROOT, JOB, job_path, validate_job
from run_specialist_report_preview import settings_hash
from run_specialist_gateway_read_verification import retain, verify_container

FOLDER=ROOT/'.work/specialist-inbox-release-20261003'
PREFIX='SPECIALIST_INBOX_IMAGE_INSPECTION '
PATHS=('src/ibkr_etl/web.py','src/ibkr_etl/agent_daily_inbox.py',
       'src/ibkr_etl/agent_daily_inbox_ui.py','src/ibkr_etl/agent_daily_report.py',
       'src/ibkr_etl/agent_daily_outbox.py','src/ibkr_etl/specialist_service_access.py')


def bundle():
    source='''import ast,hashlib,json
from pathlib import Path
paths=PATHS_LITERAL
rows=[]
for name in paths:
    path=Path('/app')/name
    payload=path.read_bytes() if path.is_file() else None
    rows.append(dict(path=name,present=payload is not None,sha256=hashlib.sha256(payload).hexdigest() if payload else None))
web=Path('/app/src/ibkr_etl/web.py').read_text(encoding='utf-8')
tree=ast.parse(web)
routes=[]
for function in ast.walk(tree):
    if isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)):
        for decorator in function.decorator_list:
            if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr=='get' and decorator.args and isinstance(decorator.args[0],ast.Constant):
                route=decorator.args[0].value
                if isinstance(route,str) and (route.startswith('/api/ai-operations/') or route=='/ai-operations'):
                    calls=[node for node in ast.walk(function) if isinstance(node,ast.Call)]
                    guarded=any(isinstance(node.func,ast.Name) and node.func.id=='_require_codex_owner' for node in calls)
                    routes.append(dict(path=route,static_database_owner_guard=guarded))
print('SPECIALIST_INBOX_IMAGE_INSPECTION '+json.dumps(dict(status='passed',files=rows,routes=routes,database_connections_opened=0,source_rows_changed=0,messages_sent=0,financial_values_logged=False)),flush=True)
'''.replace('PATHS_LITERAL',repr(PATHS))
    encoded=base64.b64encode(source.encode()).decode()
    return ['-c',"import base64;exec(compile(base64.b64decode('"+encoded+"'),'inbox-image-inspection','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def live_web():
    resource=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    containers=resource['properties']['template']['containers']
    if len(containers)!=1 or not re.fullmatch(r'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:[0-9a-f]{64}',containers[0]['image']):
        raise ValueError('Exact verified live web image required')
    return dict(image=containers[0]['image'],revision=resource['properties']['latestReadyRevisionName'])


def template(job,web):
    validate_job(job)
    value=dict(containers=copy.deepcopy(job['properties']['template']['containers']))
    value['containers'][0].update(image=web['image'],args=bundle()[0],resources=dict(cpu=0.5,memory='1Gi'))
    return value


def validate_record(record):
    expected=dict(status='passed',database_connections_opened=0,source_rows_changed=0,messages_sent=0,financial_values_logged=False)
    if any(type(record.get(k)) is not type(v) or record.get(k)!=v for k,v in expected.items()):
        raise ValueError('Successful read-only source inspection required')
    files=record.get('files')
    if not isinstance(files,list) or [r.get('path') for r in files]!=list(PATHS):raise ValueError('Unregistered inspection paths')
    for item in files:
        if type(item.get('present')) is not bool or (item['present'] and not re.fullmatch('[0-9a-f]{64}',item.get('sha256',''))):
            raise ValueError('Invalid image fingerprint')
        if not item['present'] and item.get('sha256') is not None:raise ValueError('Invalid absent file fingerprint')
    if not isinstance(record.get('routes'),list):raise ValueError('Invalid route inventory')
    for item in record['routes']:
        if item.get('path') not in ('/ai-operations','/api/ai-operations/daily-reports','/api/ai-operations/daily-reports/{report_date}') or type(item.get('static_database_owner_guard')) is not bool:
            raise ValueError('Unexpected route inspection')


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify']);p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job)
    web=live_web();wanted=template(job,web);FOLDER.mkdir(parents=True,exist_ok=True)
    intent_path=FOLDER/'intent.json';dispatch_path=FOLDER/'dispatch.json'
    if args.stage=='start':
        if intent_path.exists() or dispatch_path.exists():raise ValueError('Inspection intent exists; inspect, no implicit redispatch')
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Reporting job busy')
        intent=dict(web=web,template=wanted,payload_sha256=bundle()[1],job_settings_sha256=settings_hash(job),
            candidate_files={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PATHS})
        protected=retain(intent,'inbox-inspection-intent')
        intent_path.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=wanted)
        dispatch=dict(execution=result['name'],web=web,payload_sha256=bundle()[1],job_settings_sha256=settings_hash(job),protected_intent=protected)
        dispatch_path.write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch),flush=True);return
    dispatch=json.loads(dispatch_path.read_text())
    if args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or ''):raise ValueError('Exact inspection execution required')
    if web!=dispatch['web'] or settings_hash(job)!=dispatch['job_settings_sha256'] or bundle()[1]!=dispatch['payload_sha256']:
        raise ValueError('Website, job or inspection code changed')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],wanted['containers'][0])
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.stage=='inspect':print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)),flush=True);return
    if run['properties']['status']!='Succeeded' or len(records)!=1:raise ValueError('Image inspection not yet accepted')
    validate_record(records[0])
    candidate=json.loads(intent_path.read_text())['intent']['candidate_files']
    for name,digest in candidate.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise ValueError('Candidate changed since inspection started')
    differences=[r['path'] for r in records[0]['files'] if r['sha256']!=candidate[r['path']]]
    result=dict(status='passed',execution=args.execution,web=web,record=records[0],candidate_differences=differences,
                reporting_job_unchanged=True,website_unchanged=True,website_release_accepted=False)
    result['protected_receipt']=retain(result,'inbox-inspection-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)


if __name__=='__main__':main()
