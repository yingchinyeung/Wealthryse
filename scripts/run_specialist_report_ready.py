"""Single protected ready-event test; never changes installed worker or retries."""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_report_preview import settings_hash,grants
from run_specialist_gateway_read_verification import retain,verify_container
from run_specialist_daily_publication import FOLDER as PUBLICATION,IMAGE,prerequisites
from ibkr_etl.agent_report_notifications import ready_query

FOLDER=ROOT/'.work/specialist-ready-event-20261003'
PREFIX='SPECIALIST_READY_ACCEPTANCE '


def bundle(record):
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in ('agent_daily_inbox','agent_report_notifications'):
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'report-ready','exec'),m.__dict__)\n"
    source+='REPORT_DATE='+repr(record['report_date'])+'\nREPORT_SHA256='+repr(record['report_sha256'])+'\n'
    source+=(ROOT/'scripts/emit_specialist_report_ready.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'report-ready','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify']);p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    prerequisites()
    evidence=[]
    for path in (PUBLICATION/'acceptance.json',PUBLICATION/'browser-acceptance.json',ROOT/'.work/specialist-email-test-20261003/owner-receipt.json'):
        value=json.loads(path.read_text())
        if value.get('status')!='passed' or value.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Protected publication, browser and email-owner receipt required')
        evidence.append(value)
    if evidence[-1].get('owner_reported_received') is not True: raise ValueError('Direct owner test-email confirmation required')
    record=evidence[0]['record']
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job);grants()
    wanted=dict(containers=copy.deepcopy(job['properties']['template']['containers']))
    wanted['containers'][0].update(image=IMAGE,args=bundle(record)[0],resources=dict(cpu=0.5,memory='1Gi'))
    FOLDER.mkdir(parents=True,exist_ok=True);ip=FOLDER/'intent.json';dp=FOLDER/'dispatch.json'
    if args.stage=='start':
        if ip.exists() or dp.exists(): raise ValueError('Intent exists; inspect without re-emitting')
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Worker busy')
        intent=dict(template=wanted,payload_sha256=bundle(record)[1],job_settings_sha256=settings_hash(job),
            prerequisites=[v['protected_receipt'] for v in evidence],report_date=record['report_date'],report_sha256=record['report_sha256'])
        protected=retain(intent,'report-ready-intent')
        ip.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=wanted)
        dispatch=dict(execution=result['name'],payload_sha256=bundle(record)[1],job_settings_sha256=settings_hash(job),protected_intent=protected)
        dp.write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch));return
    dispatch=json.loads(dp.read_text())
    if (args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or '') or
            settings_hash(job)!=dispatch['job_settings_sha256'] or bundle(record)[1]!=dispatch['payload_sha256']):
        raise ValueError('Exact frozen readiness execution required')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],wanted['containers'][0])
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    rows=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.stage=='inspect':print(json.dumps(dict(status=run['properties']['status'],records=rows)));return
    expected=dict(status='passed',database_access='verified',source_table_access=False,source_mutation_authority=False,
        report_date=record['report_date'],report_sha256=record['report_sha256'],website_reverified=True,
        database_writes=0,email_submitted=False,source_rows_changed=0)
    if run['properties']['status']!='Succeeded' or len(rows)!=1 or any(type(rows[0].get(k)) is not type(v) or rows[0].get(k)!=v for k,v in expected.items()):
        raise ValueError('Successful readiness emission acceptance required')
    live=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',ready_query())
    if len(live)!=1 or live[0].get('ReportDate')!=record['report_date'] or live[0].get('ReportHash')!=record['report_sha256'] or live[0].get('NotificationKey')!=rows[0]['notification_key']:
        raise ValueError('The real readiness event must match exactly one live KQL result')
    result=dict(status='passed',execution=args.execution,record=rows[0],live_query_rows=live,
        reporting_worker_unchanged=True,dispatch=dispatch)
    result['protected_receipt']=retain(result,'report-ready-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
