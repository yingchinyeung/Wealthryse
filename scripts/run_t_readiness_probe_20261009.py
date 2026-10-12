"""Run/read one audited diagnostic execution without changing saved Azure jobs."""
from pathlib import Path
import sys
import run_alpha_ms_recovery_operation as controller

controller.FOLDER = Path(__file__).resolve().parents[1] / '.work/alpha-t-readiness-20261009-v2'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:] == ['start']:
    controller.start('readiness', 'ibkrv2stg-onboard-render',
                     'probe_t_onboarding_readiness_20261009.py', {'IBKR_RUN_MIGRATIONS': 'false'})
elif sys.argv[1:] == ['read']:
    controller.read('readiness')
else:
    raise ValueError('Expected start or read')
