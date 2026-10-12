"""Protected, fixed-date manual report build. No schedules or sender credentials."""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from datetime import date,datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_report_preview import grants,settings_hash
from run_specialist_gateway_read_verification import MODULES,retain,verify_container,FOLDER as GATEWAY_FOLDER

FOLDER=ROOT/'.work/specialist-daily-build-20261003'
PREFIX='SPECIALIST_DAILY_BUILD '


def bundle(day):
    if type(day) is not date: raise ValueError('Exact reporting date required')
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in MODULES:
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'daily-build','exec'),m.__dict__)\n"
    source+='REPORT_DATE='+repr(day.isoformat())+'\n'
    source+=(ROOT/'scripts/build_specialist_daily_report.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-daily-build','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def template(job,day):
    validate_job(job)
    value=dict(containers=copy.deepcopy(job['properties']['template']['containers']))
    value['containers'][0]['args']=bundle(day)[0]
    value['containers'][0]['resources']=dict(cpu=0.5,memory='1Gi')
    return value


def latest_closed(now):
    if now.utcoffset() is None: raise ValueError('Aware date selection required')
    local=now.astimezone(ZoneInfo('Asia/Hong_Kong'))
    return local.date() if local.hour>=18 else local.date()-timedelta(days=1)


def validate_result(record,day):
    expected=dict(status='passed',report_date=day.isoformat(),database_access='verified',
        source_table_access=False,source_mutation_authority=False,database_committed=True,
        queued_channels=['website','email'],messages_sent=0,exact_replay_verified=True,
        fresh_login_readback_verified=True,pending_deliveries_verified=2,
        website_published=False,email_submitted=False,source_rows_changed=0)
    if (any(type(record.get(k)) is not type(v) or record.get(k)!=v for k,v in expected.items())
        or type(record.get('event_count')) is not int or record['event_count']<0
        or any(not re.fullmatch('[0-9a-f]{64}',record.get(k,'')) for k in ('report_sha256','message_sha256'))):
        raise ValueError('Exact successful closed-window build required')


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify']);p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    gate=json.loads((GATEWAY_FOLDER/'read-verification-acceptance.json').read_text())
    if gate.get('status')!='passed' or gate.get('protected_receipt',{}).get('protected_readback_verified') is not True:
        raise ValueError('Independently verified real account activity required')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job);grants()
    FOLDER.mkdir(parents=True,exist_ok=True);intent_path=FOLDER/'intent.json';dispatch_path=FOLDER/'dispatch.json'
    if args.stage=='start':
        if intent_path.exists() or dispatch_path.exists():raise ValueError('Build intent exists; inspect, no automatic redispatch')
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Reporting worker busy')
        day=latest_closed(datetime.now(timezone.utc));wanted=template(job,day)
        intent=dict(report_date=day.isoformat(),payload_sha256=bundle(day)[1],job_settings_sha256=settings_hash(job),
                    template=wanted,independent_account_read_receipt=gate['protected_receipt'])
        protected=retain(intent,'daily-build-intent')
        intent_path.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=wanted)
        dispatch=dict(execution=result['name'],report_date=day.isoformat(),payload_sha256=intent['payload_sha256'],
                      job_settings_sha256=intent['job_settings_sha256'],protected_intent=protected)
        dispatch_path.write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch));return
    dispatch=json.loads(dispatch_path.read_text());day=date.fromisoformat(dispatch['report_date']);wanted=template(job,day)
    if args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or ''):raise ValueError('Exact daily-build execution required')
    if settings_hash(job)!=dispatch['job_settings_sha256'] or bundle(day)[1]!=dispatch['payload_sha256']:raise ValueError('Worker configuration or source changed')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],wanted['containers'][0])
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.stage=='inspect':print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records)));return
    if run['properties']['status']!='Succeeded' or len(records)!=1:raise ValueError('Build not accepted; inspect exact execution')
    validate_result(records[0],day)
    result=dict(execution=args.execution,status='passed',record=records[0],configuration_verified=True,
                job_settings_unchanged=True,dispatch=dispatch)
    result['protected_receipt']=retain(result,'daily-build-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
