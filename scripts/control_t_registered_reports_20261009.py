"""Dispatch a read-only registry audit on the unchanged qualified worker."""
import sys
from pathlib import Path
import run_alpha_ms_recovery_operation as controller

ROOT=Path(__file__).resolve().parents[1]
version=sys.argv[2] if len(sys.argv)==3 else 'v1'
if version not in ('v1','v2'): raise ValueError('Unsupported audit version')
controller.FOLDER=ROOT/('.work/alpha-t-registered-reports-20261009-'+version)
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1]=='start':
    job=controller.request('GET',controller.BASE+'Microsoft.App/jobs/ibkrv2stg-onboard-render?api-version=2024-03-01')
    expected='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:67a01e058a6b697e3ba0b1891ad4ec877b77fee416b09664f2e33994373d81dd'
    if version=='v2':
        import json
        proof=json.loads((ROOT/'.work/alpha-t-axis-contract-20261009-v3/cutover/ibkrv2stg-onboard-render-verified.json').read_bytes())
        if proof['status']!='passed' or not proof['settings_preserved']:raise ValueError('Verified repair deployment required')
        expected=proof['image']
    if job['properties']['template']['containers'][0]['image']!=expected: raise ValueError('Qualified worker image differs')
    controller.start('registry','ibkrv2stg-onboard-render','probe_t_registered_reports_20261009.py',{'IBKR_RUN_MIGRATIONS':'false'})
elif sys.argv[1]=='read': controller.read('registry')
else: raise ValueError('Expected start or read')
