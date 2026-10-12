"""One protected manual release of a previously verified stored daily report.

No installed worker changes, repeated dispatch, timer activation or email.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB,BASE
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_report_preview import grants,settings_hash
from run_specialist_gateway_read_verification import MODULES,retain,verify_container
from release_verified_image import invariant

FOLDER=ROOT/'.work/specialist-daily-publication-20261003'
WEB_FOLDER=ROOT/'.work/specialist-inbox-web-release-20261003'
BUILD_FOLDER=ROOT/'.work/specialist-daily-build-20261003'
PREFIX='SPECIALIST_DAILY_PUBLICATION '
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4'
REVISION='ibkrv2stg-web--0000112'


def prerequisites():
    browser=json.loads((WEB_FOLDER/'browser-acceptance.json').read_text())
    release=json.loads((WEB_FOLDER/'acceptance.json').read_text())
    build=json.loads((BUILD_FOLDER/'acceptance.json').read_text())
    for value in (browser,release,build):
        if value.get('status')!='passed' or value.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Protected prior acceptance required')
    if (browser.get('image')!=IMAGE or browser.get('website_revision')!=REVISION or
            browser.get('real_signed_in_chrome_owner_page_verified') is not True):
        raise ValueError('Exact owner website acceptance required')
    app=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    intent=json.loads((WEB_FOLDER/'intent.json').read_text())['intent']
    # Deployment invariants are independently checked against the accepted release.
    if (app['properties']['template']['containers'][0]['image']!=IMAGE or
            app['properties'].get('latestReadyRevisionName')!=REVISION or
            invariant(app,'app')!=intent['invariant_sha256']):
        raise ValueError('The accepted website release changed')
    auth=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web/authConfigs/current?api-version=2024-03-01')
    if hashlib.sha256(json.dumps(auth['properties'],sort_keys=True,separators=(',',':')).encode()).hexdigest()!=intent['auth_configuration_sha256']:
        raise ValueError('The accepted website authentication changed')
    return browser,build


def bundle(record):
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in (*MODULES,'agent_daily_inbox'):
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'daily-publication','exec'),m.__dict__)\n"
    for var,key in [('REPORT_DATE','report_date'),('REPORT_SHA256','report_sha256'),('MESSAGE_SHA256','message_sha256')]:
        source+=var+'='+repr(record[key])+'\n'
    source+=(ROOT/'scripts/publish_specialist_daily_report.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-daily-publication','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def template(job,record):
    value=dict(containers=copy.deepcopy(job['properties']['template']['containers']))
    value['containers'][0].update(image=IMAGE,args=bundle(record)[0],resources=dict(cpu=0.5,memory='1Gi'))
    return value


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify']);p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    browser,build=prerequisites();record=build['record']
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job);grants()
    if settings_hash(job)!=build['dispatch']['job_settings_sha256']: raise ValueError('Installed reporting worker changed')
    FOLDER.mkdir(parents=True,exist_ok=True);ip=FOLDER/'intent.json';dp=FOLDER/'dispatch.json'
    wanted=template(job,record);digest=bundle(record)[1]
    if args.stage=='start':
        if ip.exists() or dp.exists(): raise ValueError('Intent exists; inspect, never redispatch automatically')
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Worker busy')
        intent=dict(report_date=record['report_date'],report_sha256=record['report_sha256'],payload_sha256=digest,
            job_settings_sha256=settings_hash(job),template=wanted,build_receipt=build['protected_receipt'],
            owner_website_receipt=browser['protected_receipt'],source_mutation=False,email_submission=False)
        protected=retain(intent,'daily-publication-intent')
        ip.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=wanted)
        dispatch=dict(execution=result['name'],payload_sha256=digest,job_settings_sha256=settings_hash(job),protected_intent=protected)
        dp.write_text(json.dumps(dispatch,indent=2),encoding='utf-8');print(json.dumps(dispatch));return
    dispatch=json.loads(dp.read_text())
    if args.execution!=dispatch['execution'] or not re.fullmatch(JOB+'-[a-z0-9]{7}',args.execution or ''):
        raise ValueError('Exact publication execution required')
    if digest!=dispatch['payload_sha256'] or settings_hash(job)!=dispatch['job_settings_sha256']:
        raise ValueError('Frozen publication source or worker changed')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],wanted['containers'][0])
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    rows=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    if args.stage=='inspect': print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=rows)));return
    expected=dict(status='passed',database_access='verified',source_table_access=False,source_mutation_authority=False,
        report_date=record['report_date'],report_sha256=record['report_sha256'],message_sha256=record['message_sha256'],
        protected_report_reverified=True,fresh_login_readback_verified=True,website_published=True,
        owner_read_confirmed=False,email_submitted=False,messages_sent=0,source_rows_changed=0)
    if run['properties']['status']!='Succeeded' or len(rows)!=1 or rows[0]!=expected:
        raise ValueError('Publication acceptance unavailable; inspect, do not repeat')
    result=dict(status='passed',execution=args.execution,record=rows[0],job_settings_unchanged=True,dispatch=dispatch)
    result['protected_receipt']=retain(result,'daily-publication-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__': main()
