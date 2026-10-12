"""Immutable, read-only onboarding configuration inspection."""
from pathlib import Path
import sys
import run_alpha_ms_recovery_operation as controller

controller.FOLDER=Path(__file__).resolve().parents[1]/'.work/alpha-t-contract-probe-20261009-v1'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:]==['start']:
    controller.start('contract','ibkrv2stg-onboard-render','probe_t_contract_20261009.py',
                     {'IBKR_RUN_MIGRATIONS':'false'})
elif sys.argv[1:]==['read']:
    controller.read('contract')
else:
    raise ValueError('Expected start or read')
