"""Read-only actual-HKD independent arithmetic qualification; no cache writes."""
import hashlib,io,json,math,os,random,sys,unittest
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
os.environ['PORTAL_TEST_RUNTIME']='/app'
os.environ['RISK_DISPLAY_SOURCE']='/app/src/ibkr_etl/risk_display.py'
sys.path[:0]=['/app/src','/app/scripts','/app/upgrade-tests']
import numpy as np
from ibkr_etl import web
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.alpha_world_ms_runtime import validate_payload
from ibkr_etl.risk_snapshots import load_risk_inputs,compute_risk_snapshot,decoded_payload
from ibkr_etl.util import canonical_json
from fastapi.testclient import TestClient

def main():
    cases=unittest.TestSuite()
    for pattern in ('test_snapshot_access_correction.py','test_risk_display.py','test_ms_extended_runtime.py'):
        cases.addTests(unittest.defaultTestLoader.discover('/app/upgrade-tests',pattern=pattern))
    tests=unittest.TextTestRunner(stream=io.StringIO()).run(cases)
    if not tests.wasSuccessful() or tests.testsRun!=40:raise ValueError('Candidate regression failed')
    with web._database() as raw:
        if raw.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']!='on':raise ValueError('Read-only required')
        old=raw.execute('''SELECT as_of_date,payload_json,payload_sha256,source_data_sha256 FROM risk_analytics_snapshots
            WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND analytics_version='risk-analytics-v2'
            ORDER BY as_of_date DESC LIMIT 1''',(web.TENANT_ID,)).fetchone()
        day=str(old['as_of_date']);connection=web._portable_database_connection(raw)
        inputs,timings=load_risk_inputs(connection,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
        payload,_,source_hash=compute_risk_snapshot(connection,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day,loaded_inputs=(inputs,timings))
        validate_payload(payload)
        core,daily,evidence=analyze(inputs['ms_hkd_snapshot'])
        prior=decoded_payload(old['payload_json'])
        if any(prior['metrics'].get(k)!=v for k,v in core.items()):raise ValueError('Core differs from installed cache')
        if any(payload['metrics'].get(k)!=v for k,v in core.items()):raise ValueError('Core changed')
        if not payload['crisis_replay']:raise ValueError('Actual-date qualified crisis required')
        # Independent vectorized reconstruction of the documented seeded model.
        rng=random.Random(int.from_bytes(hashlib.sha256(','.join(f'{r:.12g}' for r in daily).encode()).digest()[:8],'big'))
        distribution=[]
        for _ in range(5000):
            starts=[rng.randint(0,len(daily)-5) for _ in range(51)]
            values=np.asarray([daily[start+j] for start in starts for j in range(5)][:252])
            wealth=np.r_[1.,np.cumprod(1+values)]
            distribution.append(float(np.min(wealth/np.maximum.accumulate(wealth)-1)))
        cutoff=np.quantile(distribution,.05)
        wealth=np.r_[1.,np.cumprod(1+np.asarray(daily[-252:]))]
        independent={'expected_maximum_drawdown_1y':float(np.mean(distribution)),
            'tail_maximum_drawdown_1y':float(np.mean([r for r in distribution if r<=cutoff])),
            'actual_maximum_drawdown_1y':float(np.min(wealth/np.maximum.accumulate(wealth)-1)),
            'blue_mean_daily_return':float(np.mean(daily)),
            'blue_standard_error':float(np.std(daily,ddof=1)/np.sqrt(len(daily)))}
        errors={k:abs(v-payload['metrics'][k]) for k,v in independent.items()}
        if max(errors.values())>1e-12:raise ValueError('Independent arithmetic differs')
        if payload['metrics']['daily_expected_tail_loss']!=core['cvar_95_daily']:raise ValueError('ETL differs')
        if payload['metrics']['crisis_maximum_drawdown']!=payload['crisis_replay']['maximum_drawdown']:raise ValueError('Crisis differs')
        after=raw.execute('''SELECT payload_sha256,source_data_sha256 FROM risk_analytics_snapshots
            WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND as_of_date=%s AND analytics_version='risk-analytics-v2' ''',(web.TENANT_ID,day)).fetchone()
        if any(after[k]!=old[k] for k in after):raise ValueError('Concurrent cache drift')
    ready=TestClient(web.app).get('/readyz').status_code
    if ready!=200:raise ValueError('Readiness failed')
    files=('web.py','agent_tools.py','qualified_agent_risk.py','risk_snapshots.py','alpha_world_ms_runtime.py')
    record={'passed':True,'tests_run':tests.testsRun,'read_only':True,'database_writes':0,'model_calls':0,
        'actual_viewer_session':False,'readiness_status':ready,'as_of':day,'return_observations':len(daily),
        'core_metrics_identical':True,'independent_absolute_errors':errors,'metrics':payload['metrics'],
        'existing_payload_sha256':old['payload_sha256'],'existing_source_sha256':old['source_data_sha256'],
        'candidate_payload_sha256':hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
        'candidate_source_sha256':source_hash,'crisis_available':True,'publication_allowed':payload['publication_allowed'],
        'source_sha256':{n:hashlib.sha256(Path('/app/src/ibkr_etl',n).read_bytes()).hexdigest() for n in files}}
    print('PLATFORM_CORRECTION_PROBE '+json.dumps(record),flush=True)

if __name__=='__main__':main()
