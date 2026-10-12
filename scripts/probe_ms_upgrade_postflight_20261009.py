"""Read-only live access/risk and report-preservation acceptance."""
import os,sys,json,math,hashlib
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
from fastapi.testclient import TestClient
required=('annualized_return','annualized_volatility','risk_free_annual_rate','sharpe_ratio','expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','var_95_daily','daily_expected_tail_loss','win_rate','blue_mean_daily_return')
records=[];reports={}
with web._database() as raw:
    if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    for sid in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
        context=AgentToolContext(web.TENANT_ID,'upgrade-live-acceptance',frozenset({sid}),'read-only-upgrade')
        response=execute_tool(web._portable_database_connection(raw),context,'get_risk_metrics',{'subject_id':sid})
        data=response.get('data') or {};metrics=data.get('metrics') or {}
        missing=[key for key in required if not isinstance(metrics.get(key),(int,float)) or not math.isfinite(metrics[key])]
        if sid=='alpha-world-ms' and missing:raise ValueError('Installed MS metrics unavailable')
        records.append({'subject':sid,'as_of':response.get('as_of'),'missing_metrics':missing})
    for table,projection in (('reporting_snapshots','snapshot_id,status,data_sha256'),('report_generation_runs','*'),('report_release_decisions','*'),('report_artifact_releases','*')):
        rows=raw.execute(f'SELECT {projection} FROM {table} WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()
        values=sorted(json.dumps(dict(row),sort_keys=True,default=str) for row in rows)
        reports[table]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(values).encode()).hexdigest()}
    caches=[dict(row) for row in raw.execute("SELECT COALESCE(payload_json->>'runtime_version','legacy-core') AS version,COUNT(*) AS rows FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' GROUP BY 1",(web.TENANT_ID,)).fetchall()]
ready=TestClient(web.app).get('/readyz').status_code
if ready!=200:raise ValueError('Database readiness failed')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'database_writes':0,'model_calls':0,
    'actual_viewer_session':False,'readiness_status':ready,'latest_risk':records,'report_and_approval_state':reports,'ms_cache_versions':caches},default=str),flush=True)
