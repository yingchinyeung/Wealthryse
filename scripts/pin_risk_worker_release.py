"""Promote the exact renderer-free candidate after both live verifications.

Only the risk job's image is mutable. This is not a general deployment tool.
Rollback image and non-image invariant are saved before the update.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from audit_azure_runtime import az, RG
from release_verified_image import invariant

SUB = '3956e821-cee4-403f-8708-e84e2ed490eb'
JOB = 'ibkrv2stg-risk-snapshots'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-risk-worker@sha256:2a84e1f5859edf1899c30ef1ffbb4df6989eda68be96051e965a05ba839633af'
PREVIOUS = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:a3f878df32c6b708448b4e67c2b2666bad8b1fc025dd265710ab1bfb1d4d7463'
EXECUTIONS = ('ibkrv2stg-risk-snapshots-nwwgvnh', 'ibkrv2stg-risk-snapshots-44txkoi')
WORKSPACE = '5264a124-10cd-45f6-93a5-6d65c37e9bfb'


def validate_execution(state, logs):
    properties = state['properties']
    containers = properties['template']['containers']
    if properties['status'] != 'Succeeded' or len(containers) != 1:
        raise RuntimeError('Candidate did not complete successfully')
    container = containers[0]
    if (container['image'] != IMAGE or container['command'] != ['python']
            or container['args'] != ['scripts/build_risk_snapshots.py']):
        raise RuntimeError('Candidate execution does not match reviewed release')
    summaries = [json.loads(row['Log_s']) for row in logs]
    expected = {'mode':'latest','skipped_existing':4,'snapshots':0,'status':'success','subjects':0}
    if not summaries or any(summary != expected for summary in summaries):
        raise RuntimeError('Candidate cache-reuse evidence is missing or unexpected')
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if az('account','show','--query','id') != SUB:
        raise RuntimeError('Unexpected subscription')
    evidence = []
    for execution in EXECUTIONS:
        state = az('containerapp','job','execution','show','-g',RG,'-n',JOB,
                   '--job-execution-name',execution)
        query = ("ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution
                 + "' | where Log_s contains 'skipped_existing' | project Log_s")
        logs = az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',query)
        evidence.append({'execution':execution,'summary':validate_execution(state,logs)})
    before = az('containerapp','job','show','-g',RG,'-n',JOB)
    containers = before['properties']['template']['containers']
    config = before['properties']['configuration']
    if (len(containers) != 1 or containers[0]['image'] not in (PREVIOUS,IMAGE)
            or config['replicaRetryLimit'] != 0
            or config['scheduleTriggerConfig']['cronExpression'] != '55 8 * * *'):
        raise RuntimeError('Risk baseline changed; re-review before promotion')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = Path(__file__).resolve().parents[1]/'.work'/'risk-worker-release'
    folder.mkdir(parents=True,exist_ok=True)
    record = {'job':JOB,'previous_image':containers[0]['image'],'rollback_image':PREVIOUS,
              'new_image':IMAGE,'configuration_sha256':invariant(before,'job'),
              'evidence':evidence,'status':'planned'}
    path = folder / ('promotion-'+stamp+'.json')
    path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    if args.apply:
        az('containerapp','job','update','-g',RG,'-n',JOB,
           '--container-name',containers[0]['name'],'--image',IMAGE)
        after = az('containerapp','job','show','-g',RG,'-n',JOB)
        if invariant(after,'job') != record['configuration_sha256']:
            raise RuntimeError('Non-image configuration changed; inspect rollback record')
        if after['properties']['template']['containers'][0]['image'] != IMAGE:
            raise RuntimeError('Image pin not verified')
        record['status'] = 'verified'
        path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'status':record['status'],'job':JOB,'evidence':str(path)}))


if __name__ == '__main__':
    main()
