"""Single-dispatch T-only recovery; retains normal worker definition unchanged."""
import sys
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-document-recovery-20261009-v1'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:]==['start']:
    job=controller.request('GET',controller.BASE+'Microsoft.App/jobs/ibkrv2stg-onboard-render?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:67a01e058a6b697e3ba0b1891ad4ec877b77fee416b09664f2e33994373d81dd': raise ValueError('Qualified worker image differs')
    controller.start('reopen','ibkrv2stg-onboard-render','reopen_t_document_validation_20261009.py',{'IBKR_RUN_MIGRATIONS':'false'})
elif sys.argv[1:]==['read']: controller.read('reopen')
else: raise ValueError('Expected start or read')
