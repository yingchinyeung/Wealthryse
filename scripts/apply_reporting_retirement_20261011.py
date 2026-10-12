"""Exact two-job retirement with recoverable alert disable and drift guards."""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import retire_reporting_legacy_20261011 as p

r=p.r; OUT=p.OUT; ROOT=p.ROOT
ALERT='ibkrv2stg-historical-reports-failed-execution'
ALERT_PATH=r.rid('Microsoft.Insights/metricAlerts', ALERT)
ALERT_API='?api-version=2018-03-01'
HISTORICAL={
    'scripts/capture_reporting_consumers_offline_20261011.py',
    'scripts/prepare_upgrade_full_legacy_20261010.py',
    'scripts/test_upgrade_full_legacy_guards_20261010.py',
    'scripts/retire_reporting_legacy_20261011.py',
}
UPDATED={'scripts/release_verified_image.py', 'infra/staging-additional-job-alerts.bicep',
         'infra/staging-historical-report-job.bicep'}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def guard_dependencies(deps):
    expected={'kind':'Microsoft.Insights/metricAlerts', 'caller':ALERT,
              'targets':['ibkrv2stg-historical-reports']}
    if deps != [expected]:raise ValueError('New or unreviewed caller; stop retirement')

def alert_body(alert, enabled):
    if alert['id'].lower()!=ALERT_PATH.lower() or alert['name']!=ALERT:
        raise ValueError('Exact retired-job alert required')
    props=alert['properties']
    if [s.lower() for s in props['scopes']] != [r.path_for('ibkrv2stg-historical-reports').lower()]:
        raise ValueError('Shared monitoring must not be disabled')
    if props['criteria'].get('odata.type')!='Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria':
        raise ValueError('Unexpected monitor type')
    result={'location':alert['location'], 'tags':alert.get('tags',{}), 'properties':copy.deepcopy(props)}
    result['properties']['enabled']=enabled
    return result

def local_scope():
    release=(ROOT/'scripts/release_verified_image.py').read_text()
    alerts=(ROOT/'infra/staging-additional-job-alerts.bicep').read_text()
    template=(ROOT/'infra/staging-historical-report-job.bicep').read_text()
    if any(n in release or n in alerts for n in p.IMAGES):raise ValueError('Retired default target remains')
    if 'param restoreRetiredHistoricalJob bool = false' not in template or "= if (restoreRetiredHistoricalJob) {" not in template:
        raise ValueError('Explicit restore-only template required')

def sources():
    return {name:sha(ROOT/name) for name in sorted(UPDATED | {'scripts/'+Path(__file__).name,
            'scripts/test_reporting_retirement_20261011.py'})}

def plan_and_entries():
    plan=r.protected(OUT/'plan.json')
    if plan['targets']!=sorted(p.IMAGES) or plan['owner_authorization']!=p.AUTHORIZATION or plan['sources']!=p.source_hashes():
        raise ValueError('Exact owner-authorized archived plan required')
    entries=[r.protected(OUT/name/'baseline.json') for name in sorted(p.IMAGES)]
    if {e['name']:e['protected_receipt'] for e in entries}!=plan['baseline_receipts']:
        raise ValueError('Archive binding differs')
    return plan,entries

def live_dependencies(plan):
    resources,deps,aux,_=r.dependency_check()
    r.preserve_others(resources,plan)
    guard_dependencies(deps)
    if aux!=plan['auxiliary_callers']:raise ValueError('Auxiliary settings changed')

def main(stage):
    if stage not in ('prepare','apply','verify'):raise ValueError('Unknown stage')
    plan,entries=plan_and_entries()
    local_scope()
    if stage=='prepare':
        if (OUT/'ready.json').exists():raise ValueError('Preserve prior recovery gate')
        classified=[]
        for item in plan['local_references']:
            name=item['path']
            if name in HISTORICAL:
                if sha(ROOT/name)!=item['sha256']:raise ValueError('Frozen historical evidence changed')
                kind='Historical explicit audit/build/test controller; no scheduled invocation found'
            elif name in UPDATED:
                kind='Retired default removed or explicit restore-only template'
            else:raise ValueError('Unreviewed local reference: '+name)
            classified.append({**item,'classification':kind,'current_sha256':sha(ROOT/name)})
        run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_retirement_20261011','-q'],
            cwd=ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
        if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['10']:
            raise ValueError('Retirement safeguards failed: '+run.stderr[-1000:])
        live_dependencies(plan)
        for e in entries:r.current_target(e)
        alert=r.request('GET',ALERT_PATH+ALERT_API)
        body=alert_body(alert,False)
        if alert['properties']['enabled'] is not True:raise ValueError('Unexpected original alert state')
        r.save(OUT,'ready',{'passed':True,'plan_receipt':plan['protected_receipt'],'sources':sources(),
            'classified_references':classified,'guard_tests':10,'guard_log':run.stderr,
            'alert_before':alert,'alert_disable_body':body,'alert_restore_body':alert_body(alert,True),
            'scope':'Delete only two approved manual jobs; disable only their dedicated obsolete monitor; preserve other workloads, identities, data, reports and approvals.',
            'restore_limit':'Job definitions recoverable from protected restore bodies. Original execution IDs cannot be recreated; 20 archived execution records and existing log retention preserved.'})
        print('Recovery, dependency and ten safety gates passed; no deletion');return
    ready=r.protected(OUT/'ready.json')
    if ready.get('passed') is not True or ready['sources']!=sources() or ready['plan_receipt']!=plan['protected_receipt']:
        raise ValueError('Protected retirement gate drift')
    if stage=='apply':
        r.runtime.safe_window();live_dependencies(plan)
        for e in entries:r.current_target(e)
        if (OUT/'delete-batch-intent.json').exists():raise ValueError('Reconcile prior deletion; never redispatch')
        current=r.request('GET',ALERT_PATH+ALERT_API)
        if current!=ready['alert_before']:raise ValueError('Monitor drift before deletion')
        r.save(OUT,'delete-batch-intent',{'targets':sorted(p.IMAGES),'ready_receipt':ready['protected_receipt'],
            'started_at':datetime.now(timezone.utc).isoformat()})
        # Disable only the now-obsolete target alert. Recovery body is protected.
        r.request('PUT',ALERT_PATH+ALERT_API,data=ready['alert_disable_body'])
        after=r.request('GET',ALERT_PATH+ALERT_API)
        if alert_body(after,False)!=ready['alert_disable_body'] or after['properties']['enabled'] is not False:
            raise ValueError('Monitor update not confirmed; no job deleted')
        r.save(OUT,'alert-disabled',{'passed':True,'id':ALERT_PATH,'properties':after['properties']})
        for e in entries:
            r.current_target(e);path=r.path_for(e['name']);folder=OUT/e['name']
            r.save(folder,'delete-intent',{'id':path,'baseline_receipt':e['protected_receipt'],'ready_receipt':ready['protected_receipt']})
            ack=r.request('DELETE',path+r.API)
            r.save(folder,'delete-ack',{'id':path,'request_returned_success':True,
                'response_sha256':hashlib.sha256(json.dumps(ack,sort_keys=True).encode()).hexdigest()})
            print(json.dumps({'retirement_submitted':e['name']}),flush=True)
        return
    batch=r.protected(OUT/'delete-batch-intent.json')
    if batch['targets']!=sorted(p.IMAGES) or batch['ready_receipt']!=ready['protected_receipt']:raise ValueError('Invalid batch intent')
    for e in entries:
        path=r.path_for(e['name']);di=r.protected(OUT/e['name']/'delete-intent.json')
        if di['id']!=path or di['baseline_receipt']!=e['protected_receipt']:raise ValueError('Deletion archive mismatch')
        if r.request('GET',path+r.API,missing=True) is not None:raise ValueError('Retired job still present')
    r.preserve_others(r.current_resources(),plan)
    alert=r.request('GET',ALERT_PATH+ALERT_API)
    if alert['properties']['enabled'] is not False or alert_body(alert,False)!=ready['alert_disable_body']:
        raise ValueError('Obsolete monitor not disabled as planned')
    if not (OUT/'verified.json').exists():
        r.save(OUT,'verified',{'passed':True,'retired_jobs':sorted(p.IMAGES),'archived_executions':sum(len(e['executions']) for e in entries),
            'disabled_monitor':ALERT,'other_workload_settings_preserved':True,'identities_databases_reports_deleted':False,
            'production_database_writes':0,'report_approvals_changed':False,'recovery_receipt':ready['protected_receipt'],
            'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print('Both approved jobs retired; original records and other workloads preserved')

if __name__=='__main__':main(*sys.argv[1:])
