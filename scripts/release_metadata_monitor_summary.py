"""Frozen source-only monitor release with canary, exact readback and rollback.

Never changes the image, service identity, credentials, schedules or portfolios.
The saved old source is the only permitted rollback target. Unknown dispatch
outcomes are reconciled, never automatically retried.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import deploy_metadata_monitor as deployment
from audit_azure_runtime import az
from run_specialist_gateway_read_verification import retain
from ibkr_etl import monitor_release as policy

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/system-improvements-20261006/monitor-summary-release-v1'
PATH=deployment.job_path(deployment.JOB)
SOURCES=('scripts/collect_operational_evidence.py','src/ibkr_etl/metadata_monitor_access.py',
    'src/ibkr_etl/operational_monitor.py','src/ibkr_etl/market_calendar.py',
    'scripts/run_metadata_shadow_monitor.py')


def read(name):
    value=json.loads((FOLDER/(name+'.json')).read_text(encoding='utf-8'))
    if value['protected_receipt']['protected_readback_verified'] is not True:
        raise ValueError('Protected immutable release record required')
    return value['record']


def save(name,record):
    path=FOLDER/(name+'.json')
    if path.exists():raise ValueError('Evidence exists; reconcile without replay')
    receipt=retain(record,'monitor-summary-'+name+'-20261006')
    with path.open('x',encoding='utf-8') as handle:
        json.dump({'record':record,'protected_receipt':receipt},handle,indent=2)


def live():
    return deployment.request('GET',PATH+'?api-version=2024-03-01')


def verify_sources(plan):
    for name,wanted in plan['source_files_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=wanted:
            raise ValueError('Source changed after frozen qualification')
    if hashlib.sha256(policy.decode_bundle(plan['args'])).hexdigest()!=plan['source_sha256']:
        raise ValueError('Frozen source digest mismatch')


def logs(execution):
    if not re.fullmatch(re.escape(policy.JOB)+r'-[a-z0-9]{7,12}',execution):
        raise ValueError('Exact monitor execution name required')
    result=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+
        execution+"' | where Log_s startswith 'METADATA_MONITOR ' | project Log_s")
    return [json.loads(row['Log_s'][17:]) for row in result]


def execution_readback(stage,baseline,plan):
    ack=read(stage+'-ack')
    native=deployment.request('GET',PATH+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if native['properties']['status'] not in {'Succeeded','Failed','Stopped'}:
        print(json.dumps({'execution':ack['execution'],'status':native['properties']['status']}));return
    records=logs(ack['execution'])
    if not records or not any(r.get('kind')=='complete' for r in records):
        print(json.dumps({'execution':ack['execution'],'awaiting_complete_telemetry':True,
            'redispatch':False}));return
    qualification=policy.qualify_execution(native,baseline,plan['args'],records,now=datetime.now(timezone.utc))
    if qualification['marker']['calendar_version']!=plan['calendar_version']:
        raise ValueError('Native calendar differs from reviewed source')
    current=live()
    expected_args=baseline['properties']['template']['containers'][0]['args'] if stage=='canary' else plan['args']
    policy.verify_readback(baseline,current,expected_args)
    save(stage+'-verified',{'qualification':qualification,'native_execution':native,'records':records,
        'persistent_configuration_verified':True,'persistent_source_changed':stage!='canary'})
    print(json.dumps({'stage':stage,'verified':True,'summary':qualification['marker']}))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('plan','canary','verify-canary','promote','reconcile','confirm','verify-confirm','rollback'))
    args=parser.parse_args()
    baseline=read('baseline');policy.validate_baseline(baseline)
    if args.stage=='plan':
        policy.verify_readback(baseline,live(),baseline['properties']['template']['containers'][0]['args'])
        bundle,digest=deployment.bundle()
        from ibkr_etl.market_calendar import CALENDAR_VERSION
        if hashlib.sha256(policy.decode_bundle(bundle)).hexdigest()!=digest:
            raise ValueError('Local bundle checksum differs')
        save('plan',{'args':bundle,'source_sha256':digest,'calendar_version':CALENDAR_VERSION,
            'source_files_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES},
            'source_files':{name:(ROOT/name).read_text(encoding='utf-8') for name in SOURCES},
            'non_source_configuration_sha256':policy.invariant(baseline),
            'image_changes':False,'grant_changes':False,'schedule_changes':False,
            'financial_producer_changes':False,'rollback_record':'baseline'})
        print(json.dumps({'planned':True,'source_sha256':digest}));return
    plan=read('plan');verify_sources(plan)
    if args.stage in {'verify-canary','verify-confirm'}:
        execution_readback(args.stage.removeprefix('verify-'),baseline,plan);return
    if args.stage=='reconcile':
        policy.verify_readback(baseline,live(),plan['args'])
        save('promotion-verified',{'source_sha256':plan['source_sha256'],
            'non_source_configuration_sha256':policy.invariant(baseline),
            'rollback_record':'baseline','image_changed':False,'credentials_changed':False,
            'schedules_changed':False,'source_data_changed':False})
        print(json.dumps({'promotion_readback_verified':True}));return
    if args.stage in {'canary','confirm'}:
        stage=args.stage
        if (FOLDER/(stage+'-intent.json')).exists():raise ValueError('Execution dispatched; readback or reconcile only')
        current=live()
        wanted=baseline['properties']['template']['containers'][0]['args'] if stage=='canary' else plan['args']
        policy.verify_readback(baseline,current,wanted)
        if stage=='confirm':read('promotion-verified')
        template={'containers':deepcopy(current['properties']['template']['containers'])}
        template['containers'][0]['args']=plan['args']
        save(stage+'-intent',{'template':template,'source_sha256':plan['source_sha256'],
            'source_writes':False,'grant_changes':False,'non_source_configuration_sha256':policy.invariant(baseline)})
        value=deployment.request('POST',PATH+'/start?api-version=2024-03-01',data=template)
        save(stage+'-ack',{'execution':value['name']})
        print(json.dumps({'execution':value['name']}));return
    if args.stage=='promote':
        qualified=read('canary-verified')['qualification']
        if qualified['source_sha256']!=plan['source_sha256']:
            raise ValueError('Canary does not qualify the frozen source')
        captured=datetime.fromisoformat(qualified['captured_at'])
        if (datetime.now(timezone.utc)-captured).total_seconds()>1200:
            raise ValueError('Canary qualification stale; do not promote')
        current=live()
        policy.verify_readback(baseline,current,baseline['properties']['template']['containers'][0]['args'])
        save('promotion-intent',{'source_sha256':plan['source_sha256'],
            'non_source_configuration_sha256':policy.invariant(baseline),'rollback_record':'baseline',
            'source_data_changed':False,'image_changed':False,'schedule_changed':False})
        deployment.request('PUT',PATH+'?api-version=2024-03-01',data=policy.source_only_definition(baseline,plan['args']))
        print(json.dumps({'submitted':True,'next':'reconcile'}));return
    # Rollback is only source-only, only to the saved existing payload, and never
    # overwrites a changed image/identity/schedule that requires separate review.
    read('promotion-intent')
    policy.verify_readback(baseline,live(),plan['args'])
    save('rollback-intent',{'restored_source_sha256':baseline['tags']['payloadSha256'],
        'non_source_configuration_sha256':policy.invariant(baseline)})
    deployment.request('PUT',PATH+'?api-version=2024-03-01',data=policy.writable_definition(baseline))
    print(json.dumps({'rollback_submitted':True,'readback_required':True}))


if __name__=='__main__':main()
