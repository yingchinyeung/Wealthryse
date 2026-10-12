"""Read-only current-date crisis coverage and consent diagnosis."""
import os,sys,json
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web

with web._database() as db:
    if db.execute("SHOW transaction_read_only").fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    records={}
    queries={
        'nav_dates':"SELECT subject_id,currency,MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=%s GROUP BY subject_id,currency ORDER BY subject_id,currency",
        'crisis_dates':"SELECT subject_id,holdings_as_of,calculation_version,COUNT(*) AS observations FROM crisis_replay_daily WHERE tenant_id=%s AND holdings_as_of>='2026-10-01' GROUP BY subject_id,holdings_as_of,calculation_version ORDER BY subject_id,holdings_as_of",
        'approvals':"SELECT subject_id,approval_type,approval_status,disclosure_version,revoked_at FROM account_onboarding_approvals WHERE tenant_id=%s AND approval_type='external_market_data' ORDER BY subject_id",
        'registrations':"SELECT subject_id,capability,operating_state,effective_to FROM subject_operating_registrations WHERE tenant_id=%s AND capability IN ('benchmark_refresh','risk_snapshot') ORDER BY subject_id,capability",
        'latest_benchmark_runs':"SELECT run_id,status,reconciliation_json FROM ingestion_runs WHERE tenant_id=%s AND run_type='staging-yfinance-benchmark' ORDER BY run_id DESC LIMIT 3",
        'mapping_summary':"SELECT subject_id,holdings_as_of,mapping_method,COUNT(*) AS holdings FROM crisis_replay_components WHERE tenant_id=%s AND holdings_as_of>='2026-10-01' GROUP BY subject_id,holdings_as_of,mapping_method ORDER BY subject_id,holdings_as_of",
    }
    for name,query in queries.items():records[name]=[dict(r) for r in db.execute(query,(web.TENANT_ID,)).fetchall()]
    for row in records['latest_benchmark_runs']:
        detail=row.pop('reconciliation_json')
        detail=json.loads(detail) if isinstance(detail,str) else detail
        row['accounts']=[{'subject':a.get('account_id'),'crisis_date':(a.get('crisis_replay') or {}).get('holdings_as_of'),
            'comparisons':[{k:b.get(k) for k in ('benchmark_id','latest_benchmark_date')} for b in a.get('benchmarks',[])]}
            for a in detail.get('accounts',[])]
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'database_writes':0,'model_calls':0,'records':records},default=str),flush=True)
