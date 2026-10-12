"""Dispatch a private candidate probe; saved Azure image and job remain unchanged."""
from pathlib import Path
import sys,json,hashlib,base64,zlib
import run_alpha_ms_recovery_operation as controller

ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-internal-crisis-cloud-20261009-v3'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:] == ['start']:
    sources={name:(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
             for name in ('alpha_world_t_crisis_policy','alpha_world_t_crisis')}
    months=[]
    for p in sorted((ROOT/'.work/alpha-t-internal-crisis-20261009-v1').glob('20*.json')):
        s=json.loads(p.read_bytes());r=s['risk']['crisis_replay']
        months.append({'snapshot':{k:s[k] for k in ('subject_id','period_end','data_as_of','reporting_currency','holdings','portfolio_summary')},
                       'maximum_drawdown':r['maximum_drawdown'],'proxy_components':r['proxy_components']})
    assert len(months)==21
    bundle={'sources':sources,'hashes':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},'months':months}
    value=base64.b64encode(zlib.compress(json.dumps(bundle).encode())).decode()
    controller.start('crisis','ibkrv2stg-onboard-render','probe_t_internal_crisis_20261009.py',
                     {'IBKR_RUN_MIGRATIONS':'false','T_CRISIS_CANDIDATE_BUNDLE':value})
elif sys.argv[1:] == ['read']:
    controller.read('crisis')
else:raise ValueError('Expected start or read')
