"""Scoped correction dispatch after exact plan/rehearsal binding; never redispatch."""
import base64,hashlib,json,sys,zlib
from pathlib import Path
import run_alpha_ms_recovery_operation as controller
from prepare_t_hkd_correction_20261009 import sha

ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-hkd-correction-20261009-v1'
action,mode=sys.argv[1:]
if mode not in ('apply','verify'):raise ValueError('Expected apply or verify')
if action=='start':
    plan=json.loads((controller.FOLDER/'plan.json').read_bytes())
    proof=json.loads((controller.FOLDER/'rehearsal-result.json').read_bytes())
    intent=json.loads((controller.FOLDER/'rehearsal-intent.json').read_bytes())
    if intent['source_sha256']!=hashlib.sha256((ROOT/'scripts/rehearse_t_hkd_correction_20261009.py').read_bytes()).hexdigest():raise ValueError('Rehearsal source changed')
    if proof['status']!='Succeeded' or not proof['job_settings_unchanged'] or len(proof['records'])!=1:raise ValueError('Successful rehearsal required')
    gate=proof['records'][0]
    if gate['plan_sha256']!=sha(plan) or not gate['rollback_verified']:raise ValueError('Exact plan rollback required')
    controller.start(mode,'ibkrv2stg-onboard-render','apply_t_hkd_configuration_20261009.py',{
        'IBKR_RUN_MIGRATIONS':'false','T_HKD_CONFIGURATION_MODE':mode,
        'T_HKD_CONFIGURATION_REHEARSAL':json.dumps(gate),
        'T_HKD_CONFIGURATION_PLAN':base64.b64encode(zlib.compress(json.dumps(plan).encode())).decode()})
elif action=='read':controller.read(mode)
else:raise ValueError('Expected start or read')
