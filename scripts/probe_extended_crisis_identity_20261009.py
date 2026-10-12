"""Read-only actual-input qualification using isolated in-memory candidate code."""
import os,sys,json,hashlib,types
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web,risk_snapshots
from ibkr_etl.util import canonical_json
from pathlib import Path

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()

with web._database() as db:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    portable=web._portable_database_connection(db)
    dates=('2025-01-22','2025-09-30','2026-09-30','2026-10-07')
    old={}
    for day in dates:
        inputs,_=risk_snapshots.load_risk_inputs(portable,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
        from ibkr_etl.alpha_world_ms_runtime import build_payload
        old[day]=build_payload(inputs)
    for item in OVERLAYS:
        path=Path('/app/src/ibkr_etl')/(item['name']+'.py')
        if item['before_sha256'] is None:
            if path.exists():raise ValueError('Unexpected installed policy module')
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=item['before_sha256']:
            raise ValueError('Installed source differs from qualified baseline')
        if hashlib.sha256(item['source'].encode()).hexdigest()!=item['after_sha256']:raise ValueError('Overlay hash differs')
        name='ibkr_etl.'+item['name']
        module=sys.modules.get(name)
        if module is None:
            module=types.ModuleType(name);module.__package__='ibkr_etl';module.__file__=str(path);sys.modules[name]=module
        exec(compile(item['source'],str(path),'exec'),module.__dict__)
    from ibkr_etl.alpha_world_ms_runtime import build_payload,validate_payload
    from ibkr_etl.alpha_world_ms_crisis import IDENTITY_VERSION
    results=[]
    for day in dates:
        inputs,_=risk_snapshots.load_risk_inputs(portable,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
        new=build_payload(inputs);validate_payload(new);previous=old[day]
        if new['source_snapshot']!=previous['source_snapshot'] or new['risk_free']!=previous['risk_free']:
            raise ValueError('Actual NAV/HIBOR inputs changed')
        if {k:v for k,v in new['metrics'].items() if k!='crisis_maximum_drawdown'} != {
            k:v for k,v in previous['metrics'].items() if k!='crisis_maximum_drawdown'}:
            raise ValueError('Non-crisis metric changed')
        crisis=new['crisis_replay']
        if previous['crisis_replay'] is not None and crisis is None:raise ValueError('Candidate lost previously available crisis')
        affected=[]
        if crisis:
            if crisis['calculation_version']!=IDENTITY_VERSION:raise ValueError('Missing crisis identity version')
            affected=[r for r in crisis['inputs']['sleeves']['U16088571']['identity_proxy_evidence']['effective_mappings'] if str(r['conid'])=='804144296']
            if any(r['source_symbol']!='SPY' or r['mapping_method']!='market_proxy' for r in affected):raise ValueError('Approved proxy missing')
        results.append({'date':day,'non_crisis_metrics_identical':True,'source_snapshot_identical':True,
            'affected_components':len(affected),'old_crisis_drawdown':previous['metrics']['crisis_maximum_drawdown'],
            'new_crisis_drawdown':new['metrics']['crisis_maximum_drawdown'],'candidate_payload_sha256':sha(new)})
    if not any(r['affected_components'] for r in results):raise ValueError('No actual affected holding tested')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'database_writes':0,
    'saved_runtime_changes':False,'source_overlays':[{'name':r['name'],'sha256':r['after_sha256']} for r in OVERLAYS],
    'samples':results}),flush=True)
