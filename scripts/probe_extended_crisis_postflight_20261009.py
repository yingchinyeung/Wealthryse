"""Independent readback of full updated rows, current agent metrics and report state."""
import os,sys,json,hashlib
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.util import canonical_json
from ibkr_etl.agent_tools import execute_tool,AgentToolContext
def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
with web._database() as db:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    rows=[r['record'] for r in db.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND analytics_version='risk-analytics-v2' AND as_of_date>='2025-01-22' AND as_of_date<='2026-10-07' ORDER BY as_of_date",(web.TENANT_ID,)).fetchall()]
    if len(rows)!=446 or sha(rows)!=EXPECTED['after_rows_sha256']:raise ValueError('Full migration readback differs')
    dates=['2025-01-22','2025-09-30','2026-09-30','2026-10-07'];samples=[]
    for day in dates:
        response=execute_tool(web._portable_database_connection(db),AgentToolContext(web.TENANT_ID,'upgrade-history-acceptance',frozenset({'alpha-world-ms'}),'read-only-upgrade'),'get_risk_metrics',{'subject_id':'alpha-world-ms','as_of':day})
        if response['quality_status']=='fail' or response['as_of']!=day or not response['data'].get('metrics'):raise ValueError('Historical assistant cache unavailable')
        samples.append({'as_of':day,'quality_status':response['quality_status']})
    reports={}
    for table in ('reporting_snapshots','report_generation_runs','report_release_decisions','report_artifact_releases'):
        values=db.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()
        reports[table]={'rows':len(values),'sha256':sha(sorted((r['record'] for r in values),key=canonical_json))}
    if reports!=EXPECTED['report_and_approval_state']:raise ValueError('Report/approval state drift')
    versions=[dict(r) for r in db.execute("SELECT COALESCE(payload_json->>'runtime_version','legacy-core') AS version,COUNT(*) AS rows FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND analytics_version='risk-analytics-v2' GROUP BY 1 ORDER BY 1",(web.TENANT_ID,)).fetchall()]
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'database_writes':0,'migrated_rows_verified':446,'full_row_hash_verified':True,'report_and_approval_state_unchanged':True,'ms_cache_versions':versions,'historical_agent_samples':samples}),flush=True)
