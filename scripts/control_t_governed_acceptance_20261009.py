"""Dispatch exactly once against the qualified installed worker, preserving jobs."""
import json,sys
from pathlib import Path
import run_alpha_ms_recovery_operation as controller

ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-governed-acceptance-20261009-v1'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:]==['read']:
    controller.read('acceptance')
elif sys.argv[1:]==['start']:
    proof=json.loads((ROOT/'.work/alpha-t-candidate-rehearsal-20261009-v3/runtime-result.json').read_bytes())
    if proof['status']!='Succeeded' or len(proof['records'])!=1 or not proof['records'][0]['onboarding_acceptance']['rollback_verified']:raise ValueError('Qualified acceptance rollback required')
    manifest=json.loads((ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker/context/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    expected='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:67a01e058a6b697e3ba0b1891ad4ec877b77fee416b09664f2e33994373d81dd'
    job=controller.request('GET',controller.BASE+'Microsoft.App/jobs/ibkrv2stg-onboard-render?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=expected:raise ValueError('Worker image drift')
    for name in ('ibkrv2stg-derived-daily','ibkrv2stg-benchmark-daily','ibkrv2stg-risk-snapshots'):
        runs=controller.request('GET',controller.BASE+'Microsoft.App/jobs/'+name+'/executions?api-version=2024-03-01')['value']
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Concurrent analytics job: '+name)
    controller.start('acceptance','ibkrv2stg-onboard-render','run_t_governed_acceptance_20261009.py',
        {'IBKR_RUN_MIGRATIONS':'false','T_ACCEPTANCE_BINDING':json.dumps({'image':expected,'installed_files_sha256':manifest['installed_files_sha256']})})
else:raise ValueError('Expected start or read')
