"""Enable only the accepted informational report-ready rule; no daily timer."""
import argparse
import copy
import json
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB,BASE
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_report_preview import settings_hash
from run_specialist_gateway_read_verification import retain
from deploy_agent_report_notifications import GROUP_PATH,RULE_PATH,fingerprint,validate_rule
from ibkr_etl.agent_report_notifications import rule_definition,ready_query

FOLDER=ROOT/'.work/specialist-notification-enable-20261003'


def wanted(enabled):
    if type(enabled) is not bool: raise ValueError('Typed notification activation required')
    value=rule_definition(workspace_id=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',action_group_id=GROUP_PATH)
    value['properties']['enabled']=enabled
    return value


def verify_only_activation_changed(previous,current):
    previous=copy.deepcopy(previous);current=copy.deepcopy(current)
    if previous.pop('enabled',None) is not False or current.pop('enabled',None) is not True:
        raise ValueError('Exact disabled-to-enabled transition required')
    # Azure updates this server-maintained provenance field to the PATCH API
    # version. Accept only the exact observed pair, not arbitrary metadata drift.
    if (previous.pop('createdWithApiVersion',None)!='2023-12-01' or
            current.pop('createdWithApiVersion',None)!='2021-08-01' or
            fingerprint(previous)!=fingerprint(current)):
        raise ValueError('Unexpected rule configuration change')
    return dict(previous='2023-12-01',current='2021-08-01')


def prerequisites():
    result=[]
    for path,field in [(ROOT/'.work/specialist-email-test-20261003/owner-receipt.json','owner_reported_received'),
                       (ROOT/'.work/specialist-daily-publication-20261003/browser-acceptance.json','real_signed_in_chrome_owner_detail_verified'),
                       (ROOT/'.work/specialist-ready-event-20261003/acceptance.json','reporting_worker_unchanged')]:
        value=json.loads(path.read_text())
        if value.get('status')!='passed' or value.get(field) is not True or value.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Protected real recipient, owner detail and readiness acceptance required')
        result.append(value)
    expected=result[-1]['live_query_rows']
    live=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',ready_query())
    if len(live)!=1 or live!=expected: raise ValueError('Exact current published-report readiness required')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','verify']);args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    receipts=prerequisites()
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job)
    group=request('GET',GROUP_PATH+'?api-version=2023-01-01')
    rule=request('GET',RULE_PATH+'?api-version=2023-12-01')
    FOLDER.mkdir(parents=True,exist_ok=True);ip=FOLDER/'intent.json'
    if args.stage=='start':
        if ip.exists(): raise ValueError('Activation intent exists; verify without repeating')
        validate_rule(rule,wanted(False))
        if fingerprint(group['properties'])!=receipts[0]['dispatch']['baseline']['group_sha256']:
            raise ValueError('Accepted notification receiver changed')
        intent=dict(operation='enable one informational ready rule only',rule=RULE_PATH,
            previous_enabled=False,next_enabled=True,previous_rule=rule,
            action_group_sha256=fingerprint(group['properties']),job_sha256=settings_hash(job),
            prerequisite_receipts=[v['protected_receipt'] for v in receipts],
            daily_schedule_enabled=False,source_rows_changed=0)
        protected=retain(intent,'ready-rule-enable-intent')
        ip.write_text(json.dumps(dict(intent=intent,protected_receipt=protected),indent=2),encoding='utf-8')
        # Official partial-update API changes only this flag. No rule recreation,
        # action-group edits, new mailbox access or existing job schedule changes.
        request('PATCH',RULE_PATH+'?api-version=2021-08-01',data={'properties':{'enabled':True}})
        print(json.dumps(dict(activation_requested=True,daily_schedule_enabled=False)));return
    intent=json.loads(ip.read_text())['intent']
    validate_rule(rule,wanted(True))
    api_stamp=verify_only_activation_changed(intent['previous_rule']['properties'],rule['properties'])
    if (settings_hash(job)!=intent['job_sha256'] or
            fingerprint(group['properties'])!=intent['action_group_sha256']):
        raise ValueError('Unexpected notification or existing worker configuration change')
    result=dict(status='passed',notification_rule_enabled=True,only_functional_enabled_flag_changed=True,
        server_api_version_metadata_change=api_stamp,
        reporting_worker_manual=True,daily_schedule_enabled=False,existing_action_group_unchanged=True,
        recipient_delivery_confirmed=False,source_rows_changed=0,
        ready_report_date=receipts[-1]['record']['report_date'])
    result['protected_receipt']=retain(result,'ready-rule-enable-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
