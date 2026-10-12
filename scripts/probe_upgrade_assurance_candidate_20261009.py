"""Actual assurance evaluation using the qualified current application runtime."""
import os,sys,json,hashlib
from pathlib import Path
from urllib.parse import urlsplit
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
os.environ['IBKR_RUN_MIGRATIONS']='false'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import connect
from ibkr_etl.agent_tools import AgentToolContext
from ibkr_etl.portfolio_assurance import collect_assurance_inputs,evaluate_portfolio_assurance
if urlsplit(os.environ['DATABASE_URL']).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected diagnostic database')
for name,digest in EXPECTED_FILES.items():
    if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Unchanged assurance entrypoint/policy differs')
tenant=os.environ['PORTFOLIO_TENANT_ID']
if tenant!='00000000-0000-0000-0000-000000000001':raise ValueError('Exact tenant required')
db=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],tenant);records=[]
try:
    if db.execute("SELECT current_setting('transaction_read_only') AS setting").fetchone()['setting']!='on':raise ValueError('Read-only connection required')
    for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
        context=AgentToolContext(tenant,'upgrade-assurance-readonly',frozenset({subject}),'qualified-runtime-no-model')
        values,reports=collect_assurance_inputs(db,context,subject_id=subject)
        result=evaluate_portfolio_assurance(values,reports,subject_id=subject)
        mapped={v['tool_name']:v.get('data') or {} for v in values};nav=mapped['get_performance_series'].get('series') or [];comparison=mapped['compare_with_benchmarks'].get('series') or [];risk=mapped['get_risk_metrics']
        records.append({'subject':subject,'status':result['status'],'as_of':result.get('as_of'),'checks_failed':result['checks_failed'],'checks_passed':result['checks_passed'],'checks_warned':result['checks_warned'],'findings':result['findings'],'performance_endpoints':[nav[0],nav[-1]] if nav else [],'benchmark_endpoints':[comparison[0],comparison[-1]] if comparison else [],'benchmark_start_nav':next((n for n in nav if str(n['date'])==str(comparison[0]['date'])),None) if comparison else None,'risk_period':{k:(risk.get('analytics_evidence') or {}).get(k) for k in ('start','end','version','nav_observations','reporting_currency')},'full_nav_total_return':float(nav[-1]['nav'])/float(nav[0]['nav'])-1 if len(nav)>1 else None,'reported_total_return':(risk.get('metrics') or {}).get('total_return')})
finally:db.rollback();db.close()
passed=len(records)==5 and all(r['checks_failed']==0 and r['status']!='fail' for r in records)
print('ASSURANCE_CANDIDATE_PROOF '+json.dumps({'passed':passed,'records':records,'database_writes':0,'model_calls':0,'verified_assurance_files':EXPECTED_FILES},default=str),flush=True)
if not passed:raise SystemExit(1)
