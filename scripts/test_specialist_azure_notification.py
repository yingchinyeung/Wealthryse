"""One owner-only Azure test email; does not enable rules or daily schedules.

202 is dispatch acceptance, and Completed is provider completion, not a human
receipt. Unknown dispatches are never repeated automatically.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlsplit
import requests
from azure.identity import AzureCliCredential
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_report_preview import settings_hash
from run_specialist_gateway_read_verification import retain
from deploy_agent_report_notifications import GROUP_PATH,RULE_PATH,fingerprint,validate_rule
from ibkr_etl.agent_report_notifications import rule_definition
from deploy_metadata_monitor import BASE

FOLDER=ROOT/'.work/specialist-email-test-20261003'
PUBLICATION=ROOT/'.work/specialist-daily-publication-20261003'
RECIPIENT='anthony.ying@wealthryse.ai'
API='2021-09-01'


def status_path(location):
    value=urlsplit(location)
    prefix=GROUP_PATH+'/notificationStatus/'
    if (value.scheme!='https' or value.netloc!='management.azure.com' or value.fragment or
            not value.path.lower().startswith(prefix.lower()) or
            not re.fullmatch('[A-Za-z0-9-]{1,128}',value.path[len(prefix):]) or
            value.query not in ('api-version=2021-09-01','api-version=2022-06-01')):
        raise ValueError('Unregistered Azure notification status URL')
    return value.path+'?'+value.query


def provider_completion(value):
    rows=value.get('actionDetails',[])
    if len(rows)!=1: return False
    try:
        sent=datetime.fromisoformat(rows[0].get('SendTime',''))
        if sent.utcoffset() is None or sent.year<2026: return False
    except (ValueError,TypeError): return False
    return (value.get('state')=='Completed' and len(rows)==1 and
        rows[0].get('MechanismType')=='Email' and rows[0].get('Name')=='operations-email' and
        rows[0].get('Status')=='Completed' and rows[0].get('SubState')=='Default' and
        isinstance(rows[0].get('SendTime'),str) and rows[0]['SendTime']!='0001-01-01T00:00:00+00:00')


def guards():
    for name in ('acceptance.json','browser-acceptance.json'):
        value=json.loads((PUBLICATION/name).read_text())
        if value.get('status')!='passed' or value.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Published report and real owner detail acceptance required')
    group=request('GET',GROUP_PATH+'?api-version=2023-01-01')
    rows=[r for r in group['properties'].get('emailReceivers',[]) if r.get('name')=='operations-email']
    if (not group['properties'].get('enabled') or len(rows)!=1 or
            rows[0].get('emailAddress')!=RECIPIENT or rows[0].get('status')!='Enabled'):
        raise ValueError('Exact existing enabled owner email receiver required')
    rule=request('GET',RULE_PATH+'?api-version=2023-12-01')
    validate_rule(rule,rule_definition(workspace_id=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',action_group_id=GROUP_PATH))
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job)
    return dict(group_sha256=fingerprint(group['properties']),rule_sha256=fingerprint(rule['properties']),
        job_sha256=settings_hash(job)),dict(name='operations-email',emailAddress=RECIPIENT,
            useCommonAlertSchema=rows[0].get('useCommonAlertSchema',False))


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','inspect','verify','record-owner-receipt']);args=p.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    state,receiver=guards();FOLDER.mkdir(parents=True,exist_ok=True)
    ip=FOLDER/'intent.json';dp=FOLDER/'dispatch.json'
    if args.stage=='start':
        if ip.exists() or dp.exists(): raise ValueError('Test intent exists; inspect without resending')
        body=dict(alertType='logalertv2',emailReceivers=[receiver])
        intent=dict(scope='one Azure synthetic test email only',recipient=RECIPIENT,body=body,
            baseline=state,recipient_delivery_confirmed=False,source_data_included=False,
            production_rule_enabled=False,worker_schedule_changed=False)
        protected=retain(intent,'azure-email-test-intent')
        ip.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        token=AzureCliCredential().get_token('https://management.azure.com/.default').token
        try:
            response=requests.post('https://management.azure.com'+GROUP_PATH+'/createNotifications?api-version='+API,
                headers={'Authorization':'Bearer '+token},json=body,timeout=60)
        finally: del token
        dispatch=dict(http_status=response.status_code,protected_intent=protected,baseline=state)
        # Store the known response before any parsing can fail. Intent protects
        # ambiguous network outcomes too; callers must inspect rather than resend.
        dp.write_text(json.dumps(dispatch,indent=2),encoding='utf-8')
        if response.status_code==202:
            location=response.headers.get('Location','')
            # A failed route check must not discard the routing evidence of an
            # accepted send. Retain only a credential-free location shape and
            # hash; never follow an unregistered endpoint or resend the email.
            parsed=urlsplit(location)
            dispatch['response_location_sha256']=hashlib.sha256(location.encode()).hexdigest()
            dispatch['response_location_shape']=dict(scheme=parsed.scheme,hostname=parsed.hostname,
                path=parsed.path,query_keys=[part.split('=',1)[0] for part in parsed.query.split('&') if part])
            dp.write_text(json.dumps(dispatch,indent=2),encoding='utf-8')
            dispatch['status_path']=status_path(location)
        elif response.status_code==200: dispatch['result']=response.json()
        else: raise RuntimeError('Azure test dispatch HTTP '+str(response.status_code))
        dp.write_text(json.dumps(dispatch,indent=2),encoding='utf-8')
        print(json.dumps(dict(http_status=response.status_code,dispatch_accepted=True,
            recipient_delivery_confirmed=False,test_only=True)));return
    dispatch=json.loads(dp.read_text())
    if state!=dispatch['baseline']: raise ValueError('Existing rule, receiver or reporting worker changed')
    if args.stage=='record-owner-receipt':
        if dispatch.get('http_status') not in (200,202): raise ValueError('Known accepted dispatch required')
        result=dict(status='passed',recipient=RECIPIENT,provider_dispatch_accepted=True,
            provider_completion_verified=False,owner_reported_received=True,
            confirmation_source='Direct human reply to the exact test email question: Yes, received',
            test_only=True,full_report_emailed=False,notification_rule_enabled=False,
            reporting_worker_unchanged=True,existing_action_group_unchanged=True,source_rows_changed=0,
            status_poll_unavailable='The initial accepted response Location was rejected and not retained; no resend',
            dispatch=dispatch)
        result['protected_receipt']=retain(result,'azure-email-test-owner-receipt')
        (FOLDER/'owner-receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result));return
    value=request('GET',dispatch['status_path']) if 'status_path' in dispatch else dispatch.get('result')
    if not isinstance(value,dict): raise ValueError('Known dispatch status unavailable; no resend')
    safe=dict(state=value.get('state'),actions=[{k:r.get(k) for k in ('MechanismType','Name','Status','SubState','SendTime')} for r in value.get('actionDetails',[])])
    if args.stage=='inspect': print(json.dumps(safe));return
    if not provider_completion(value): raise ValueError('Provider test not completed successfully; no resend')
    result=dict(status='passed',provider_status=safe,recipient=RECIPIENT,provider_test_completed=True,
        recipient_delivery_confirmed=False,test_only=True,full_report_emailed=False,
        existing_action_group_unchanged=True,reporting_worker_unchanged=True,notification_rule_enabled=False,
        source_rows_changed=0,dispatch=dispatch)
    result['protected_receipt']=retain(result,'azure-email-test-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__': main()
