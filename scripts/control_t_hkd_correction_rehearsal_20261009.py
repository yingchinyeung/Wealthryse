"""One-shot temporary-only HKD metadata correction rehearsal."""
import base64,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from prepare_t_hkd_correction_20261009 import sha

ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-hkd-correction-20261009-v1'
if sys.argv[1:]==['start']:
    plan=json.loads((controller.FOLDER/'plan.json').read_bytes())
    controller.start('rehearsal','ibkrv2stg-onboard-render','rehearse_t_hkd_correction_20261009.py',{
        'IBKR_RUN_MIGRATIONS':'false','T_HKD_CORRECTION_SHA256':sha(plan),
        'T_HKD_CORRECTION_PLAN':base64.b64encode(zlib.compress(json.dumps(plan).encode())).decode()})
elif sys.argv[1:]==['read']:controller.read('rehearsal')
else:raise ValueError('Expected start or read')
