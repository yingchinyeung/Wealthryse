"""Read-only source/approval preservation and live daily-cycle acceptance."""
import os,sys,json,hashlib,math
from datetime import date,datetime,timezone
from urllib.parse import urlsplit
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient
from ibkr_etl.util import canonical_json
from ibkr_etl import web
from ibkr_etl.subject_read_contract import ACTIVE_NAV_CURRENCY_SQL
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
from ibkr_etl.market_calendar import benchmark_session_on_or_before

MODE=globals().get('MODE','preflight')
EXPECTED=['U16088571','U16088606','alpha-world-ms','alpha-world-t','hong-kong-china']
PROTECTED=('accounts','nav_snapshots','position_snapshots','cash_snapshots','cash_transactions','trades','corporate_actions','fx_rates','flex_rows','subject_calculation_definitions','report_artifact_releases','report_release_decisions','reporting_snapshots','report_generation_runs')
if MODE not in ('preflight','postflight') or urlsplit(os.environ['DATABASE_URL']).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected live audit scope')
service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
tables={}
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=20) as db:
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    db.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    for table in PROTECTED:
        h=hashlib.sha256();count=0
        with db.cursor(name='live_cycle_preservation') as cursor:
            cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier(table)))
            for row in cursor:h.update((row['digest']+'\n').encode());count+=1
        tables[table]={'rows':count,'sha256':h.hexdigest()}
    subjects=[r['subject_id'] for r in db.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id",(web.TENANT_ID,)).fetchall()]
    # PostgreSQL's collation need not match Python's Unicode sorting. Scope is
    # exact membership and cardinality, not database-dependent ordering.
    if len(subjects)!=len(EXPECTED) or set(subjects)!=set(EXPECTED):raise ValueError('Active scope changed')
records=[];latest_dates={};proxy=[]
required=('annualized_return','annualized_volatility','risk_free_annual_rate','sharpe_ratio','expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','var_95_daily','daily_expected_tail_loss','win_rate','blue_mean_daily_return')
with web._database() as db:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only consumer required')
    portable=web._portable_database_connection(db)
    for sid in EXPECTED:
        latest=str(portable.execute('SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=? AND subject_id=? AND currency='+ACTIVE_NAV_CURRENCY_SQL,(web.TENANT_ID,sid)).fetchone()['latest'])
        latest_dates[sid]=latest
        if MODE=='postflight':
            context=AgentToolContext(web.TENANT_ID,'upgrade-live-cycle-acceptance',frozenset({sid}),'read-only-live-cycle')
            for tool in ('get_risk_metrics','compare_with_benchmarks'):
                value=execute_tool(portable,context,tool,{'subject_id':sid});data=value.get('data') or {};metrics=data.get('metrics') or {}
                missing=[k for k in required if not isinstance(metrics.get(k),(int,float)) or not math.isfinite(metrics[k])] if tool=='get_risk_metrics' else []
                last,expected=None,None
                if tool=='compare_with_benchmarks':
                    series=data.get('series') or [];last=series[-1]['date'] if series else None
                    expected=benchmark_session_on_or_before(data['benchmark']['benchmark_id'],date.fromisoformat(latest)).isoformat()
                records.append({'subject':sid,'tool':tool,'as_of':value.get('as_of'),'latest_nav':latest,'quality':value.get('quality_status'),'missing_metrics':missing,'last_benchmark_observation':last,'expected_benchmark_session':expected})
    proxy=[dict(r) for r in db.execute("SELECT conid,source_symbol,mapping_method,proxy_reason FROM crisis_replay_components WHERE tenant_id=%s AND subject_id='U16088571' AND holdings_as_of='2026-10-08' AND source_symbol='SPY' ORDER BY conid",(web.TENANT_ID,)).fetchall()]
differences=[]
if MODE=='postflight':
    receipt=globals()['BEFORE'];raw=service.get_blob_client('internal-reports',receipt['private_blob']).download_blob().readall()
    if hashlib.sha256(raw).hexdigest()!=receipt['sha256']:raise ValueError('Protected preflight differs')
    before=json.loads(raw)
    if before['mode']!='preflight' or not before['passed']:raise ValueError('Passing preflight required')
    differences=[table for table in PROTECTED if before['protected_tables'][table]!=tables[table]]
    passed=not differences and len(records)==10 and all(day=='2026-10-08' for day in latest_dates.values()) and all(r['quality']!='fail' and not r['missing_metrics'] and r['as_of']==r['latest_nav'] and r['last_benchmark_observation']==r['expected_benchmark_session'] for r in records)
else:
    passed=all(latest_dates[sid]=='2026-10-08' for sid in ('U16088571','U16088606')) and all(latest_dates[sid] in ('2026-10-07','2026-10-08') for sid in EXPECTED)
result={'passed':passed,'mode':MODE,'checked_at':datetime.now(timezone.utc).isoformat(),'database_writes':0,'active_subjects':subjects,'protected_tables':tables,'protected_table_differences':differences,'latest_dates':latest_dates,'consumer_records':records,'spy_proxy_components':proxy,'real_viewer_acceptance':False}
raw=canonical_json(result).encode();digest=hashlib.sha256(raw).hexdigest();name='review/platform-upgrade-20261009/live-cycle-'+MODE+'-'+digest+'.json'
blob=service.get_blob_client('internal-reports',name)
try:blob.upload_blob(raw,overwrite=False)
except ResourceExistsError:pass  # Accept only after exact content verification below.
if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Audit readback differs')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':passed,'mode':MODE,'database_writes':0,'protected_table_differences':differences,'latest_dates':latest_dates,'consumer_records':records,'private_blob':name,'sha256':digest}),flush=True)
if not passed:raise SystemExit(1)
