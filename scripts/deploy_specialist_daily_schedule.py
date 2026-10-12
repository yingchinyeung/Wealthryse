"""Staged daily worker rollout with frozen intent, rehearsal and rollback data."""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB
from deploy_specialist_report_worker import ROOT, JOB, job_path, validate_job, definition
from run_specialist_report_preview import grants, settings_hash
from run_specialist_gateway_read_verification import MODULES, retain, verify_container
from deploy_agent_report_notifications import GROUP_PATH, RULE_PATH, fingerprint, validate_rule
from enable_specialist_ready_notification import wanted as ready_definition
from release_verified_image import invariant

FOLDER=ROOT/'.work/specialist-daily-schedule-20261003'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4'
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'
CRON='0 10 * * *'


def bundle(rehearsal=False):
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in (*MODULES,'agent_daily_inbox','agent_report_notifications','agent_daily_worker'):
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'daily-runtime','exec'),m.__dict__)\n"
    if rehearsal: source+="REPLAY_DATE='2026-10-02'\n"
    source+=(ROOT/'scripts/run_scheduled_specialist_report.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-daily-worker','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def wanted(scheduled=False):
    value=definition()
    value['tags'].update(mode='daily-publisher-scheduled' if scheduled else 'daily-publisher-manual',
                         firstReportDate='2026-10-03',payloadSha256=bundle()[1])
    p=value['properties']; p['configuration']['replicaTimeout']=600
    p['template']['containers'][0].update(image=IMAGE,args=bundle()[0])
    if scheduled:
        p['configuration'].pop('manualTriggerConfig')
        p['configuration'].update(triggerType='Schedule',scheduleTriggerConfig=dict(
            cronExpression=CRON,parallelism=1,replicaCompletionCount=1))
    return value


def validate(current, scheduled=False):
    target=wanted(scheduled)
    if current.get('identity',{}).get('type')!='UserAssigned':
        raise ValueError('Only the registered reporting identity is permitted')
    if fingerprint(current.get('identity',{}).get('userAssignedIdentities')) != fingerprint(target['identity']['userAssignedIdentities']):
        # ARM expands values with principal/client IDs: compare IDs, not read-only metadata.
        if {x.lower() for x in current.get('identity',{}).get('userAssignedIdentities',{})}!={x.lower() for x in target['identity']['userAssignedIdentities']}:
            raise ValueError('Reporting identity changed')
    p=current['properties']; t=target['properties']
    if p.get('environmentId','').casefold()!=t['environmentId'].casefold(): raise ValueError('Execution environment changed')
    a=p['configuration']; b=t['configuration']
    for field in ('triggerType','replicaTimeout','replicaRetryLimit','manualTriggerConfig','scheduleTriggerConfig'):
        differs = a.get(field)!=b[field] if field in b else a.get(field) not in (None,{})
        if differs:
            raise ValueError('Daily execution controls differ: '+field)
    def bindings(rows): return sorted((s.get('name'),s.get('keyVaultUrl'),s.get('identity','').lower()) for s in rows)
    def registries(rows):
        # Azure serializes unused optional registry credentials as empty strings.
        # Only None/empty defaults are equivalent; nonempty credentials fail.
        def optional(value): return None if value in (None,'') else value
        return [(r.get('server'),r.get('identity','').lower(),optional(r.get('username')),
                 optional(r.get('passwordSecretRef'))) for r in rows]
    if (bindings(a.get('secrets',[]))!=bindings(b['secrets']) or
            any('value' in s and s['value'] for s in a.get('secrets',[])) or
            registries(a.get('registries',[]))!=registries(b['registries'])):
        raise ValueError('Restricted credential/registry bindings changed')
    containers=p['template']['containers']; expected=t['template']['containers'][0]
    if len(containers)!=1 or any(containers[0].get(k)!=expected[k] for k in ('name','image','command','args','env')):
        raise ValueError('Frozen daily runtime differs')
    if any(containers[0].get('resources',{}).get(k)!=v for k,v in expected['resources'].items()):
        raise ValueError('Unexpected daily worker capacity')
    if (p['template'].get('volumes') or p['template'].get('initContainers')
            or a.get('eventTriggerConfig') or containers[0].get('volumeMounts') or containers[0].get('probes')):
        raise ValueError('Unregistered runtime extension')
    for k,v in target['tags'].items():
        if current.get('tags',{}).get(k)!=v: raise ValueError('Reviewed worker tags differ')
    if p.get('provisioningState')!='Succeeded': raise ValueError('Worker provisioning has not completed')


def jobs_page_path(link):
    target=urlsplit(link)
    params=parse_qs(target.query)
    if (target.scheme!='https' or target.netloc!='management.azure.com' or target.fragment
            or target.path.casefold()!=(BASE+'Microsoft.App/jobs').casefold()
            or params.get('api-version')!=['2024-03-01']
            or set(params)!={'api-version','$skiptoken'} or len(params['$skiptoken'])!=1):
        raise ValueError('Unregistered job-inventory continuation')
    return target.path+'?'+target.query


def unaffected(*, baseline=None):
    jobs=[]; path=BASE+'Microsoft.App/jobs?api-version=2024-03-01'; seen=set()
    for _ in range(10):
        if path in seen: raise ValueError('Repeated job-inventory page')
        seen.add(path)
        page=request('GET',path); jobs.extend(page.get('value',[]))
        if not page.get('nextLink'): break
        path=jobs_page_path(page['nextLink'])
    else: raise ValueError('Bounded job inventory incomplete')
    values={}
    for job in jobs:
        if job['name']==JOB: continue
        if job['name'] in values: raise ValueError('Duplicate job in Azure inventory')
        p=job['properties']
        # Never retain environment/credential values from other jobs.
        values[job['name']]=fingerprint(dict(identity=job.get('identity'),tags=job.get('tags'),
            configuration=p.get('configuration'),template=p.get('template'),environmentId=p.get('environmentId')))
    group=request('GET',GROUP_PATH+'?api-version=2023-01-01')
    ready=request('GET',RULE_PATH+'?api-version=2023-12-01'); validate_rule(ready,ready_definition(True))
    app=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web?api-version=2024-03-01')
    result=dict(other_jobs=values,group_sha256=fingerprint(group['properties']),
                ready_rule_sha256=fingerprint(ready['properties']),website_invariant_sha256=invariant(app,'app'))
    # The first immutable rehearsal intent predates discovery of Azure's
    # pagination. Verify exactly those named rows, without claiming the earlier
    # snapshot covered all jobs. Installation takes a NEW complete baseline.
    if baseline is not None and not baseline.get('job_inventory_complete'):
        result['other_jobs']={name:values.get(name) for name in baseline['other_jobs']}
    else: result['job_inventory_complete']=True
    return result


def save(name, record, kind):
    record['protected_receipt']=retain(record,kind)
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/name).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k not in ('template','definition','rollback_definition')},sort_keys=True),flush=True)


def accepted(path):
    value=json.loads(path.read_text())
    receipt=value.get('protected_receipt',{})
    payload={k:v for k,v in value.items() if k!='protected_receipt'}
    if (value.get('status')!='passed' or receipt.get('protected_readback_verified') is not True
            or fingerprint(payload)!=receipt.get('sha256')
            or not receipt.get('blob','').endswith('-'+receipt.get('sha256','')+'.json')):
        raise ValueError('Protected prerequisite acceptance required')
    return value


def execution_receipt(job, execution, template, stage):
    if not re.fullmatch(JOB+r'-[a-z0-9]{7,12}',execution or ''): raise ValueError('Exact execution name required')
    run=request('GET',job_path(JOB)+'/executions/'+execution+'?api-version=2024-03-01')
    verify_container(run['properties']['template']['containers'],template['containers'][0])
    prefix='SPECIALIST_DAILY_CYCLE '
    rows=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"' | where Log_s startswith '"+prefix+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(prefix)) for r in rows]
    if stage=='inspect':
        print(json.dumps(dict(execution=execution,status=run['properties']['status'],records=records))); return None
    if run['properties']['status']!='Succeeded' or len(records)!=1: raise ValueError('Successful complete worker evidence required')
    row=records[0]
    for k,v in dict(status='passed',database_access='verified',source_table_access=False,source_mutation_authority=False,
                    email_submitted=False,messages_sent=0,source_rows_changed=0,website_release_verified=True).items():
        if type(row.get(k)) is not type(v) or row.get(k)!=v: raise ValueError('Daily worker result differs: '+k)
    if stage=='verify-rehearsal':
        publication=accepted(ROOT/'.work/specialist-daily-publication-20261003/acceptance.json')['record']
        if row.get('mode')!='rehearsal' or row.get('processed')!=[dict(report_date='2026-10-02',
            report_sha256=publication['report_sha256'],event_count=0,already_published=True,fresh_login_readback_verified=True)]:
            raise ValueError('Immutable replay acceptance differs')
    else:
        if row.get('mode')!='scheduled' or row.get('processed')!=[] or row.get('catch_up_pending')!=0:
            raise ValueError('Unexpected pre-deadline production smoke effects')
    return dict(status='passed',execution=execution,record=row)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=[
        'rehearse','verify-rehearsal','inspect','install','verify-install','test-installed','verify-smoke','schedule','verify-schedule'])
    parser.add_argument('--execution'); args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01'); grants()
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.stage=='rehearse':
        validate_job(job)
        accepted(ROOT/'.work/specialist-notification-enable-20261003/owner-receipt.json')
        accepted(ROOT/'.work/specialist-daily-watchdog-20261003/acceptance.json')
        if (FOLDER/'rehearsal-intent.json').exists(): raise ValueError('Rehearsal already requested; inspect instead')
        template=copy.deepcopy(wanted()['properties']['template']); template['containers'][0]['args']=bundle(True)[0]
        baseline=unaffected()
        intent=dict(payload_sha256=bundle(True)[1],job_sha256=settings_hash(job),template=template,unaffected=baseline,
                    source_rows_changed=0,notification_emission=False)
        save('rehearsal-intent.json',intent,'daily-worker-rehearsal-intent')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=template)
        (FOLDER/'rehearsal-dispatch.json').write_text(json.dumps(dict(execution=result['name'])),encoding='utf-8')
        print(json.dumps(dict(execution=result['name']))); return
    if args.stage in ('verify-rehearsal','inspect','verify-smoke'):
        rehearsal=args.stage in ('verify-rehearsal','inspect')
        intent=json.loads((FOLDER/('rehearsal-intent.json' if rehearsal else 'smoke-intent.json')).read_text())
        dispatch=json.loads((FOLDER/('rehearsal-dispatch.json' if rehearsal else 'smoke-dispatch.json')).read_text())
        if args.execution!=dispatch['execution']: raise ValueError('Exact retained dispatch required')
        if intent['payload_sha256']!=bundle(rehearsal)[1] or unaffected(baseline=intent['unaffected'])!=intent['unaffected']:
            raise ValueError('Frozen source or unrelated resource changed')
        if rehearsal:
            validate_job(job)
            if settings_hash(job)!=intent['job_sha256']: raise ValueError('Installed worker changed during rehearsal')
        else: validate(job)
        result=execution_receipt(job,args.execution,intent['template'],args.stage)
        if result: save('rehearsal-acceptance.json' if rehearsal else 'smoke-acceptance.json',result,
                        'daily-worker-rehearsal-completed' if rehearsal else 'daily-worker-smoke-completed')
        return
    if args.stage=='install':
        validate_job(job); rehearsal=accepted(FOLDER/'rehearsal-acceptance.json')
        prior=json.loads((FOLDER/'rehearsal-intent.json').read_text())
        if unaffected(baseline=prior['unaffected'])!=prior['unaffected']: raise ValueError('Unrelated resources changed')
        complete_baseline=unaffected()
        if (FOLDER/'install-intent.json').exists(): raise ValueError('Installation already requested; verify')
        value=wanted(); rollback=definition()
        save('install-intent.json',dict(definition=value,rollback_definition=rollback,
            baseline_job_sha256=settings_hash(job),unaffected=complete_baseline,rehearsal_receipt=rehearsal['protected_receipt']),
            'daily-worker-install-intent')
        request('PATCH',job_path(JOB)+'?api-version=2024-03-01',data=dict(tags=value['tags'],
            properties=dict(template=value['properties']['template'],configuration=dict(replicaTimeout=600))))
        print(json.dumps(dict(installation_requested=True,trigger='Manual'))); return
    if args.stage=='verify-install':
        validate(job)
        intent=json.loads((FOLDER/'install-intent.json').read_text())
        if unaffected()!=intent['unaffected']: raise ValueError('Unrelated resources changed')
        save('install-acceptance.json',dict(status='passed',payload_sha256=bundle()[1],trigger='Manual',
            other_jobs_unchanged=len(intent['unaffected']['other_jobs']),rollback_preserved=True), 'daily-worker-installed'); return
    if args.stage=='test-installed':
        validate(job); accepted(FOLDER/'install-acceptance.json')
        if (FOLDER/'smoke-intent.json').exists(): raise ValueError('Production smoke already requested; verify')
        if datetime.now(timezone.utc)>=datetime(2026,10,3,10,tzinfo=timezone.utc): raise ValueError('Pre-deadline smoke expired')
        intent=dict(payload_sha256=bundle()[1],template=wanted()['properties']['template'],unaffected=unaffected())
        save('smoke-intent.json',intent,'daily-worker-smoke-intent')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01')
        (FOLDER/'smoke-dispatch.json').write_text(json.dumps(dict(execution=result['name'])),encoding='utf-8')
        print(json.dumps(dict(execution=result['name']))); return
    if args.stage=='schedule':
        validate(job); smoke=accepted(FOLDER/'smoke-acceptance.json')
        watchdog=accepted(ROOT/'.work/specialist-daily-watchdog-20261003/acceptance.json')
        if watchdog.get('enabled') is not True: raise ValueError('Live watchdog acceptance required first')
        from deploy_specialist_report_watchdog import RULE_PATH as WATCHDOG_PATH,wanted as watchdog_wanted
        validate_rule(request('GET',WATCHDOG_PATH+'?api-version=2023-12-01'),watchdog_wanted(True))
        if datetime.now(timezone.utc)>=datetime(2026,10,3,10,tzinfo=timezone.utc): raise ValueError('Initial deadline reached; reconcile before schedule')
        if (FOLDER/'schedule-intent.json').exists(): raise ValueError('Schedule already requested; verify')
        value=wanted(True); baseline=json.loads((FOLDER/'install-intent.json').read_text())['unaffected']
        if unaffected()!=baseline: raise ValueError('Unrelated resources changed before activation')
        save('schedule-intent.json',dict(definition=value,unaffected=baseline,smoke_receipt=smoke['protected_receipt'],
            watchdog_receipt=watchdog['protected_receipt'],cron_utc=CRON,time_zone='Asia/Hong_Kong',daily_hour=18), 'daily-worker-schedule-intent')
        # The official API uses JSON Merge Patch. Explicit null removes the
        # former manual trigger; credentials, capacity and payload stay intact.
        request('PATCH',job_path(JOB)+'?api-version=2024-03-01',data=dict(tags=value['tags'],properties=dict(configuration=dict(
            triggerType='Schedule',manualTriggerConfig=None,
            scheduleTriggerConfig=value['properties']['configuration']['scheduleTriggerConfig']))))
        print(json.dumps(dict(schedule_requested=True,cron_utc=CRON,hong_kong_hour=18))); return
    validate(job,True)
    intent=json.loads((FOLDER/'schedule-intent.json').read_text())
    if unaffected()!=intent['unaffected']: raise ValueError('Unrelated resources changed during scheduling')
    save('schedule-acceptance.json',dict(status='passed',daily_schedule_enabled=True,cron_utc=CRON,
        time_zone='Asia/Hong_Kong',daily_hour=18,first_report_date='2026-10-03',payload_sha256=bundle()[1],
        immediate_retries=0,parallelism=1,other_jobs_unchanged=len(intent['unaffected']['other_jobs']),
        source_mutation_authority=False,watchdog_enabled=True,natural_scheduled_execution_verified=False,
        six_autonomous_specialists_active=False,owner_codex_runtime_active=False), 'daily-worker-schedule-completed')


if __name__=='__main__':main()
