"""Verify notification KQL and install one dormant Azure informational rule.

No mailbox grants, model calls, email sends, enabled timers or website changes.
Protect intent first and verify readback. Never implicitly replace an existing
rule or enable it before authenticated website acceptance.
"""
import argparse
import copy
import hashlib
import json
from datetime import date, datetime, timezone

from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB
from deploy_specialist_report_worker import ROOT, JOB, job_path, validate_job
from run_specialist_report_preview import settings_hash
from run_specialist_gateway_read_verification import retain
from ibkr_etl.agent_report_notifications import (
    TENANT, RULE, JOB as PRODUCER, PREFIX, serialize_ready_event, ready_query, rule_definition,
)

WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'
GROUP_PATH=BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts'
RULE_PATH=BASE+'Microsoft.Insights/scheduledQueryRules/'+RULE
FOLDER=ROOT/'.work/specialist-notifications-20261003'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def synthetic_query():
    key=hashlib.sha256(f'specialist-report-ready-v1:{TENANT}:2026-10-03'.encode()).hexdigest()
    event=dict(schema='specialist-report-ready-v1',tenant_id=TENANT,report_date='2026-10-03',
        report_sha256='a'*64,notification_key=key,recorded_at='2026-10-03T10:01:00+00:00',
        website_state='available',recipient_delivery_confirmed=False)
    line=serialize_ready_event(event)
    cases=[('valid',[line,line],PRODUCER+'-abcdefg'),
        ('unpublished',[line.replace('"available"','"pending"')],PRODUCER+'-abcdefg'),
        ('wrong_producer',[line],'other-job-abcdefg'),
        ('wrong_tenant',[line.replace(TENANT,'00000000-0000-0000-0000-000000000002')],PRODUCER+'-abcdefg'),
        ('forged_key',[line.replace(key,'f'*64)],PRODUCER+'-abcdefg'),
        ('false_delivery_claim',[line.replace('"recipient_delivery_confirmed":false','"recipient_delivery_confirmed":true')],PRODUCER+'-abcdefg'),
        ('wrong_boolean_type',[line.replace('"recipient_delivery_confirmed":false','"recipient_delivery_confirmed":"false"')],PRODUCER+'-abcdefg'),
        ('stale_report',[line.replace('2026-10-03','2026-10-01')],PRODUCER+'-abcdefg'),
        ('future_report',[line.replace('2026-10-03','2026-10-05')],PRODUCER+'-abcdefg')]
    parts=[]
    for name,lines,producer in cases:
        rows=','.join("datetime(2026-10-03T10:02:00Z),'"+producer+"','"+v+"'" for v in lines)
        table='datatable(TimeGenerated:datetime,ContainerGroupName_s:string,Log_s:string)['+rows+']'
        query=ready_query().replace('ContainerAppConsoleLogs_CL','('+table+')')
        query=query.replace('ago(1d)','datetime(2026-10-02T10:05:00Z)').replace('now()','datetime(2026-10-03T10:05:00Z)')
        parts.append('('+query+'\n| summarize Matches=count() | extend Case="'+name+'")')
    return 'union '+',\n'.join(parts)+'\n| project Case,Matches'


def validate_rule(actual,wanted):
    if actual.get('kind')!='LogAlert': raise ValueError('Unexpected notification rule kind')
    actual=actual['properties'];expected=wanted['properties']
    for field in ('displayName','description','enabled','severity','scopes','evaluationFrequency','windowSize',
                  'overrideQueryTimeRange','autoMitigate','skipQueryValidation','criteria','actions'):
        if actual.get(field)!=expected[field]: raise ValueError('Notification rule differs: '+field)


def main():
    p=argparse.ArgumentParser();p.add_argument('--deploy-dormant',action='store_true');args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    group=request('GET',GROUP_PATH+'?api-version=2023-01-01')
    props=group['properties']
    if not props.get('enabled') or not any(r.get('emailAddress')=='anthony.ying@wealthryse.ai'
        and r.get('status')=='Enabled' for r in props.get('emailReceivers',[])):
        raise ValueError('Existing enabled owner email receiver required')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job)
    wanted=rule_definition(workspace_id=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs', action_group_id=GROUP_PATH)
    tests=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',synthetic_query())
    expected={name:0 for name in ('unpublished','wrong_producer','wrong_tenant','forged_key','false_delivery_claim','wrong_boolean_type','stale_report','future_report')}
    expected['valid']=1
    if {r['Case']:int(r['Matches']) for r in tests}!=expected:raise ValueError('Live KQL synthetic acceptance failed')
    live=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',ready_query())
    if live:raise ValueError('Readiness events already exist; investigate before new rule deployment')
    existing=request('GET',RULE_PATH+'?api-version=2023-12-01',missing=True)
    if existing:validate_rule(existing,wanted)
    record=dict(status='passed',rule=RULE,synthetic_cases=expected,live_ready_events=0,
        receiver='anthony.ying@wealthryse.ai',microsoft365_sender_enabled=False,
        notification_enabled=False,emails_sent=0,source_rows_changed=0,job_settings_sha256=settings_hash(job),
        action_group_properties_sha256=fingerprint(props),rule_configuration_sha256=fingerprint(wanted),deployed=False)
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.deploy_dormant:
        intent=dict(record=record,definition=wanted,operation='create_or_verify_exact_dormant_rule')
        record['protected_intent']=retain(intent,'notification-intent')
        # Recheck bindings after retaining the intent, before the Azure write.
        if fingerprint(request('GET',GROUP_PATH+'?api-version=2023-01-01')['properties'])!=fingerprint(props):
            raise ValueError('Action group changed before deployment')
        if not existing:request('PUT',RULE_PATH+'?api-version=2023-12-01',data=wanted)
        validate_rule(request('GET',RULE_PATH+'?api-version=2023-12-01'),wanted)
        if settings_hash(request('GET',job_path(JOB)+'?api-version=2024-03-01'))!=record['job_settings_sha256']:
            raise ValueError('Reporting worker changed during deployment')
        if fingerprint(request('GET',GROUP_PATH+'?api-version=2023-01-01')['properties'])!=record['action_group_properties_sha256']:
            raise ValueError('Existing notification group changed')
        record.update(deployed=True,reporting_job_unchanged=True,existing_action_group_unchanged=True)
        record['protected_receipt']=retain(record,'notification-completed')
    (FOLDER/'acceptance.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
