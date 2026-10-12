"""Protected new rule rollout. Existing notification group/jobs stay unchanged."""
import argparse
import json
from datetime import datetime, timezone
from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB
from deploy_specialist_report_worker import ROOT, JOB, job_path
from deploy_agent_report_notifications import GROUP_PATH, fingerprint, validate_rule
from run_specialist_report_preview import settings_hash
from run_specialist_gateway_read_verification import retain
from ibkr_etl.agent_report_notifications import SCHEMA, TENANT, serialize_ready_event
from ibkr_etl.agent_report_watchdog import RULE, missing_query, watchdog_definition, FIRST_PERIOD_END
from enable_specialist_ready_notification import verify_only_activation_changed
import hashlib

FOLDER=ROOT/'.work/specialist-daily-watchdog-20261003'
RULE_PATH=BASE+'Microsoft.Insights/scheduledQueryRules/'+RULE
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'


def wanted(enabled=False):
    return watchdog_definition(workspace_id=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',
                               action_group_id=GROUP_PATH, enabled=enabled)


def activation_patch():
    # Scheduled-rule PATCH replaces the tag map; send the full reviewed map.
    return {'tags':wanted(True)['tags'],'properties':{'enabled':True}}


def synthetic_query():
    key=hashlib.sha256(f'{SCHEMA}:{TENANT}:2026-10-03'.encode()).hexdigest()
    event=dict(schema=SCHEMA, tenant_id=TENANT, report_date='2026-10-03', report_sha256='a'*64,
        notification_key=key, recorded_at='2026-10-03T10:01:00+00:00',
        website_state='available', recipient_delivery_confirmed=False)
    valid=serialize_ready_event(event)
    cases=[('before_activation','2026-10-03T09:50:00Z',[],0),
           ('within_grace','2026-10-03T10:14:59Z',[],0),
           ('never_started','2026-10-03T10:15:00Z',[],1),
           ('validated_publication','2026-10-03T10:20:00Z',[valid,valid],0),
           ('wrong_hash','2026-10-03T10:20:00Z',[valid.replace('a'*64,'not-a-hash')],1),
           ('wrong_tenant','2026-10-03T10:20:00Z',[valid.replace(TENANT,'00000000-0000-0000-0000-000000000002')],1),
           ('unpublished','2026-10-03T10:20:00Z',[valid.replace('"available"','"pending"')],1),
           ('future_publication','2026-10-03T10:20:00Z',[valid.replace('2026-10-03','2026-10-04')],1),
           ('job_success_without_publication','2026-10-03T10:20:00Z',['SPECIALIST_DAILY_CYCLE {"status":"passed"}'],1)]
    parts=[]; results=[]
    for index,(name,now,lines,_) in enumerate(cases):
        rows=','.join("datetime(2026-10-03T10:02:00Z),'"+JOB+"-abcdefg','"+line+"'" for line in lines)
        table='datatable(TimeGenerated:datetime,ContainerGroupName_s:string,Log_s:string)['+rows+']'
        query=missing_query().replace('ContainerAppConsoleLogs_CL','('+table+')')
        query=query.replace('ago(1d)', 'datetime('+now+') - 1d').replace('now()', 'datetime('+now+')')
        for variable in ('ExpectedEnd','ExpectedDate','ValidPublication'):
            query=query.replace(variable,variable+str(index))
        declarations,body=query.split('\nprint ',1)
        result_name='Case'+str(index); results.append(result_name)
        parts.append(declarations+'\nlet '+result_name+' = (print '+body+
                     '\n| summarize Matches=count() | extend Case="'+name+'");')
    return '\n'.join(parts)+'\nunion '+','.join(results)+'\n| project Case,Matches', {name:n for name,_,_,n in cases}


def write_record(name, record, kind):
    record['protected_receipt']=retain(record, kind)
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/name).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record),flush=True)


def main():
    p=argparse.ArgumentParser(); p.add_argument('stage',choices=['install','verify','enable','repair-tags']); args=p.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    owner=json.loads((ROOT/'.work/specialist-notification-enable-20261003/owner-receipt.json').read_text())
    if owner.get('owner_reported_received') is not True or owner.get('protected_receipt',{}).get('protected_readback_verified') is not True:
        raise ValueError('Protected real notice recipient confirmation required')
    group=request('GET',GROUP_PATH+'?api-version=2023-01-01')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01')
    ip=FOLDER/'intent.json'
    if args.stage=='install':
        if ip.exists(): raise ValueError('Watchdog intent exists; verify before another write')
        if request('GET',RULE_PATH+'?api-version=2023-12-01',missing=True): raise ValueError('Watchdog already exists')
        query,expected=synthetic_query()
        rows=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',query)
        if {r['Case']:int(r['Matches']) for r in rows}!=expected:
            raise ValueError('Independent live KQL acceptance failed')
        intent=dict(definition=wanted(), synthetic_cases=expected,
            group_sha256=fingerprint(group['properties']), job_sha256=settings_hash(job),
            owner_receipt=owner['protected_receipt'], source_rows_changed=0)
        write_record('intent.json',intent,'daily-watchdog-intent')
        request('PUT',RULE_PATH+'?api-version=2023-12-01',data=intent['definition'])
        print(json.dumps(dict(watchdog_installed=True, enabled=False))); return
    intent=json.loads(ip.read_text())
    current=request('GET',RULE_PATH+'?api-version=2023-12-01')
    if fingerprint(group['properties'])!=intent['group_sha256']:
        raise ValueError('Existing notification group changed')
    if args.stage=='enable':
        receipt=json.loads((ROOT/'.work/specialist-daily-schedule-20261003/rehearsal-acceptance.json').read_text())
        if receipt.get('status')!='passed' or receipt.get('protected_receipt',{}).get('protected_readback_verified') is not True:
            raise ValueError('Protected restricted-runtime rehearsal required')
        if datetime.now(timezone.utc)>=datetime.fromisoformat(FIRST_PERIOD_END.replace('Z','+00:00')):
            raise ValueError('First reporting deadline reached; reconcile rollout before activation')
        validate_rule(current,wanted(False))
        if (FOLDER/'enable-intent.json').exists(): raise ValueError('Watchdog activation already requested; inspect')
        write_record('enable-intent.json',dict(rule=RULE_PATH, before=current['properties'],
            previous_tags=current.get('tags',{}),next_tags=wanted(True)['tags'],
            rehearsal_receipt=receipt['protected_receipt'], job_sha256=settings_hash(job)), 'daily-watchdog-enable-intent')
        request('PATCH',RULE_PATH+'?api-version=2021-08-01',data=activation_patch())
        current=request('GET',RULE_PATH+'?api-version=2023-12-01')
    if args.stage=='repair-tags':
        # The first accepted enable request sent a partial tags map; this API
        # replaces that map. Restore ONLY the planned application/purpose tags,
        # never repeat enablement or change any functional alert property.
        validate_rule(current,wanted(True))
        activation=json.loads((FOLDER/'enable-intent.json').read_text())
        verify_only_activation_changed(activation['before'],current['properties'])
        if current.get('tags')!={'mode':'active'}: raise ValueError('Unexpected tag repair target')
        if (FOLDER/'tag-repair-intent.json').exists(): raise ValueError('Tag repair already requested; verify')
        write_record('tag-repair-intent.json',dict(rule=RULE_PATH,previous_tags=current['tags'],
            next_tags=wanted(True)['tags'],functional_properties_sha256=fingerprint(current['properties']),
            reason='Azure PATCH replaces the tag map; restore the exact originally planned tags'), 'daily-watchdog-tag-repair-intent')
        request('PATCH',RULE_PATH+'?api-version=2021-08-01',data={'tags':wanted(True)['tags']})
        current=request('GET',RULE_PATH+'?api-version=2023-12-01')
    enabled=current['properties']['enabled']
    validate_rule(current,wanted(enabled))
    if any(current.get('tags',{}).get(k)!=v for k,v in wanted(enabled)['tags'].items()):
        raise ValueError('Watchdog status tags differ')
    if enabled:
        activation=json.loads((FOLDER/'enable-intent.json').read_text())
        verify_only_activation_changed(activation['before'],current['properties'])
        repair=FOLDER/'tag-repair-intent.json'
        if repair.exists() and fingerprint(current['properties'])!=json.loads(repair.read_text())['functional_properties_sha256']:
            raise ValueError('Functional alert settings changed during tag repair')
    # Readiness rule/group is not modified; only this new watchdog is installed.
    rows=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',missing_query())
    if rows: raise ValueError('Unexpected current missing publication; investigate')
    if not enabled and settings_hash(job)!=intent['job_sha256']:
        raise ValueError('Worker changed during dormant watchdog installation')
    result=dict(status='passed',watchdog_rule=RULE,enabled=enabled,
        first_report_date='2026-10-03',grace_minutes=15,live_missing_publications=0,
        synthetic_cases=intent['synthetic_cases'],existing_action_group_unchanged=True,
        source_rows_changed=0, recipient_delivery_confirmed=False)
    write_record('acceptance.json',result,'daily-watchdog-enabled' if enabled else 'daily-watchdog-dormant')


if __name__=='__main__': main()
