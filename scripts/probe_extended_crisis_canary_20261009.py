"""Actual tool dispatch with a qualified in-memory cache, never a live DB update."""
import os,sys,json,hashlib,io,unittest
from pathlib import Path
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
os.environ['PORTAL_TEST_RUNTIME']='/app'
sys.path[:0]=['/app/src','/app/scripts','/app/identity-tests']
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload
from ibkr_etl.util import canonical_json
from fastapi.testclient import TestClient

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()

suite=unittest.TestSuite()
for pattern in ('test_snapshot_access_correction.py','test_ms_agent_tenant_propagation.py'):
    suite.addTests(unittest.defaultTestLoader.discover('/app/identity-tests',pattern=pattern))
tests=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
if not tests.wasSuccessful() or tests.testsRun!=18:raise ValueError('Access regression failed')

with web._database() as raw:
    if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    portable=web._portable_database_connection(raw)
    day=str(raw.execute("SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE tenant_id=%s AND subject_id='alpha-world-ms'",(web.TENANT_ID,)).fetchone()['day'])
    candidate,_,source=compute_risk_snapshot(portable,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
    old=raw.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND as_of_date=%s AND analytics_version='risk-analytics-v2'",(web.TENANT_ID,day)).fetchone()['record']
    original=decoded_payload(old['payload_json'])
    if {k:v for k,v in original['metrics'].items() if k!='crisis_maximum_drawdown'}!={k:v for k,v in candidate['metrics'].items() if k!='crisis_maximum_drawdown'}:raise ValueError('Non-crisis metrics changed')
    replacement={**old,'payload_json':candidate,'payload_sha256':sha(candidate),'source_data_sha256':source,'quality_status':candidate['quality_status']}
    class Cursor:
        def fetchone(self):return replacement
    class InMemoryCache:
        def __init__(self):self.replacements=0
        def execute(self,sql,args=()):
            if 'FROM risk_analytics_snapshots' in sql:
                normalized=' '.join(sql.split())
                expected='SELECT payload_json,payload_sha256,quality_status,comparison_version FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s'
                if normalized!=expected or tuple(map(str,args))!=(str(web.TENANT_ID),'alpha-world-ms',day,'risk-analytics-v2'):
                    raise ValueError('Unexpected cache interception scope')
                self.replacements+=1;return Cursor()
            return portable.execute(sql,args)
        def __getattr__(self,name):return getattr(portable,name)
    connection=InMemoryCache()
    response=execute_tool(connection,AgentToolContext(web.TENANT_ID,'upgrade-proxy-canary',frozenset({'alpha-world-ms'}),'read-only-upgrade'),
        'get_risk_metrics',{'subject_id':'alpha-world-ms','as_of':day})
    if connection.replacements!=1 or response['quality_status']=='fail' or response['data']['metrics']!=candidate['metrics']:
        raise ValueError('Candidate tool/website cache agreement failed')
    after=raw.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND as_of_date=%s AND analytics_version='risk-analytics-v2'",(web.TENANT_ID,day)).fetchone()['record']
    if after!=old:raise ValueError('Live cache changed')
if TestClient(web.app).get('/readyz').status_code!=200:raise ValueError('Readiness failed')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'database_writes':0,'model_calls':0,
    'tests_run':tests.testsRun,'readiness_status':200,'in_memory_cache_only':True,'actual_viewer_session':False,
    'exact_tool_dispatch_passed':True,'non_crisis_metrics_identical':True,'as_of':day,'candidate_payload_sha256':sha(candidate)}),flush=True)
