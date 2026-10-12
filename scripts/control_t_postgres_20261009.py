"""One-shot temporary-table rehearsal; persistent job settings unchanged."""
from pathlib import Path
import sys,json,hashlib,base64,zlib
import run_alpha_ms_recovery_operation as controller
from ibkr_etl.alpha_world_t_presentation import build_model
ROOT=Path(__file__).resolve().parents[1]
controller.FOLDER=ROOT/'.work/alpha-t-postgres-qualification-20261009-v4'
controller.FOLDER.mkdir(exist_ok=True)
if sys.argv[1:]==['start']:
    names=('subject_onboarding','market_calendar','hibor','alpha_world_ms_analytics','derived_portfolio',
        'alpha_world_t_analytics','alpha_world_t_cache','alpha_world_t_crisis_policy','alpha_world_t_crisis',
        'alpha_world_t_presentation','alpha_world_t_reporting','alpha_world_t_runtime','risk_snapshots',
        'website_benchmarks','agent_tools','reporting_snapshot')
    sources={n:(ROOT/'src/ibkr_etl'/(n+'.py')).read_text(encoding='utf-8') for n in names}
    months=[]
    for p in sorted((ROOT/'.work/alpha-t-reporting-batch-20261009-v2/inputs').glob('20*.json')):
        s=json.loads(p.read_bytes());m=build_model(s)
        months.append({'period_end':s['period_end'],'metrics':m['risk']})
    if len(months)!=21:raise ValueError('Expected 21 qualified snapshots')
    bundle={'sources':sources,'hashes':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()},
        'months':months,'nav':s['performance']['series'],
        'expected_subject_state':[{'subject_id':'alpha-world-t','lifecycle_status':'onboarding','base_currency':'USD','reporting_currency':'USD'}]}
    encoded=base64.b64encode(zlib.compress(json.dumps(bundle).encode())).decode()
    parts=[encoded[i:i+24000] for i in range(0,len(encoded),24000)]
    env={f'T_QUALIFICATION_{i}':v for i,v in enumerate(parts)}
    env.update(IBKR_RUN_MIGRATIONS='false',T_QUALIFICATION_PARTS=str(len(parts)))
    controller.start('postgres','ibkrv2stg-onboard-render','qualify_t_postgres_20261009.py',env)
elif sys.argv[1:]==['read']:controller.read('postgres')
else:raise ValueError('Expected start or read')
