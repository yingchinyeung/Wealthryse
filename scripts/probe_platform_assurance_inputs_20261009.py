"""Read-only deterministic diagnostics; never invoke a model or alter records."""
import os,sys,json,math
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
REQUIRED=('annualized_return','annualized_volatility','risk_free_annual_rate','sharpe_ratio','expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','var_95_daily','daily_expected_tail_loss','win_rate','blue_mean_daily_return')
def finite(v):
    try:return math.isfinite(float(v))
    except (TypeError,ValueError):return False
def main():
    records=[]
    with web._database() as raw:
        raw.execute('SET LOCAL statement_timeout=120000')
        assert raw.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']=='on'
        subjects=raw.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id",(web.TENANT_ID,)).fetchall()
        for subject in subjects:
            sid=subject['subject_id'];context=AgentToolContext(web.TENANT_ID,'platform-correction-diagnostic',frozenset({sid}),'platform-correction-read-only')
            for tool in ('get_risk_metrics','compare_with_benchmarks'):
                try:
                    value=execute_tool(web._portable_database_connection(raw),context,tool,{'subject_id':sid})
                    data=value.get('data') or {};metrics=data.get('metrics') or {}
                    records.append({'subject':sid,'tool':tool,'quality':value.get('quality_status'),'as_of':value.get('as_of'),
                      'missing_metrics':[k for k in REQUIRED if not finite(metrics.get(k))] if tool=='get_risk_metrics' else [],
                      'series_count':len(data.get('series') or []),'data_keys':list(data),
                      'status':data.get('status'),'cache_status':data.get('cache_status'),
                      'quality_detail':data.get('quality'),'risk_keys':list(metrics),
                      'message':data.get('message'),'crisis_keys':list(data.get('crisis_replay') or {})})
                except Exception as error:
                    records.append({'subject':sid,'tool':tool,'error_class':type(error).__name__,'error_code':getattr(error,'code',None)})
        runs=raw.execute("SELECT account_id,reconciliation_json FROM (SELECT reconciliation_json, reconciliation_json::jsonb->>'account_id' AS account_id, ROW_NUMBER() OVER (PARTITION BY reconciliation_json::jsonb->>'account_id' ORDER BY run_id DESC) AS n FROM ingestion_runs WHERE tenant_id=%s AND run_type='staging-snapshot-import' AND status='success') q WHERE n=1",(web.TENANT_ID,)).fetchall()
        cash=[]
        for row in runs:
            report=row['reconciliation_json'];report=json.loads(report) if isinstance(report,str) else report
            cash.append({'account_id':row['account_id'],'destination_only':report.get('destination_only_rows',report.get('destination_only'))})
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'model_calls':0,'records':records,'cash_reconciliation':cash},default=str),flush=True)
if __name__=='__main__':main()
