"""Actual tool dispatch acceptance, plus unchanged cache/report fingerprints."""
import os,sys,json,hashlib,math,io,unittest
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
os.environ['PORTAL_TEST_RUNTIME']='/app'
sys.path[:0]=['/app/src','/app/scripts','/app/upgrade-tests']
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
from ibkr_etl.risk_snapshots import decoded_payload
from ibkr_etl.util import canonical_json
from fastapi.testclient import TestClient
def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
suite=unittest.TestSuite()
for pattern in ('test_snapshot_access_correction.py','test_ms_agent_tenant_propagation.py'):
    suite.addTests(unittest.defaultTestLoader.discover('/app/upgrade-tests',pattern=pattern))
tests=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
if not tests.wasSuccessful() or tests.testsRun!=18:raise ValueError('Access regression failed')
records=[];reports={}
with web._database() as raw:
    if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    for sid in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
        context=AgentToolContext(web.TENANT_ID,'upgrade-live-acceptance',frozenset({sid}),'read-only-upgrade')
        response=execute_tool(web._portable_database_connection(raw),context,'get_risk_metrics',{'subject_id':sid})
        data=response.get('data') or {};metrics=data.get('metrics') or {}
        required=('annualized_return','annualized_volatility','risk_free_annual_rate','sharpe_ratio','expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','var_95_daily','daily_expected_tail_loss','win_rate','blue_mean_daily_return')
        missing=[key for key in required if not isinstance(metrics.get(key),(int,float)) or not math.isfinite(metrics[key])]
        if sid=='alpha-world-ms':
            if missing or response['quality_status']=='fail':raise ValueError('MS full tool dispatch failed')
            row=raw.execute("SELECT payload_json,payload_sha256 FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version='risk-analytics-v2'",(web.TENANT_ID,sid,response['as_of'])).fetchone()
            if metrics!=decoded_payload(row['payload_json'])['metrics']:raise ValueError('Tool and website cache metrics differ')
            ms_hash=row['payload_sha256']
        records.append({'subject':sid,'as_of':response.get('as_of'),'missing_metrics':missing,'quality_status':response['quality_status']})
    for table in ('reporting_snapshots','report_generation_runs','report_release_decisions','report_artifact_releases'):
        rows=raw.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()
        reports[table]={'rows':len(rows),'sha256':sha(sorted((r['record'] for r in rows),key=canonical_json))}
if TestClient(web.app).get('/readyz').status_code!=200:raise ValueError('Readiness failed')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'database_writes':0,'model_calls':0,'actual_viewer_session':False,'tests_run':tests.testsRun,'readiness_status':200,'latest_risk':records,'ms_payload_sha256':ms_hash,'report_and_approval_state':reports,'source_sha256':hashlib.sha256(Path('/app/src/ibkr_etl/agent_tools.py').read_bytes()).hexdigest()}),flush=True)
