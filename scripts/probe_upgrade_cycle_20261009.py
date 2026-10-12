"""Read-only isolated-cycle acceptance: broker preservation and actual consumers."""
import os,sys,json,hashlib,math
from datetime import date
from urllib.parse import urlsplit,urlunsplit
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

source=os.environ['DATABASE_URL'];parts=urlsplit(source)
if parts.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected source')
userinfo,sep,_=parts.netloc.rpartition('@')
if not sep:raise ValueError('Existing credentials required')
target=urlunsplit((parts.scheme,userinfo+'@ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'+(':'+str(parts.port) if parts.port else ''),parts.path,parts.query,parts.fragment))
protected=('accounts','nav_snapshots','position_snapshots','cash_snapshots','cash_transactions','trades','corporate_actions','fx_rates','flex_rows','subject_calculation_definitions','report_artifact_releases','report_release_decisions','reporting_snapshots')

def fingerprints(uri):
    result={}
    with psycopg.connect(uri,row_factory=dict_row,connect_timeout=20) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SELECT set_config('app.tenant_id','00000000-0000-0000-0000-000000000001',true)")
        for table in protected:
            h=hashlib.sha256();n=0
            with db.cursor(name='cycle_preserved_rows') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier(table)))
                for row in cursor:h.update((row['digest']+'\n').encode());n+=1
            result[table]={'rows':n,'sha256':h.hexdigest()}
    return result

live,clone=fingerprints(source),fingerprints(target)
differences=[name for name in protected if live[name]!=clone[name]]
os.environ['DATABASE_URL']=target
from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolContext,execute_tool
from ibkr_etl.subject_read_contract import ACTIVE_NAV_CURRENCY_SQL
from ibkr_etl.util import canonical_json
from ibkr_etl.market_calendar import benchmark_session_on_or_before
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
required=('annualized_return','annualized_volatility','risk_free_annual_rate','sharpe_ratio','expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','var_95_daily','daily_expected_tail_loss','win_rate','blue_mean_daily_return')
records=[]
with web._database() as db:
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    portable=web._portable_database_connection(db)
    for sid in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):
        latest=portable.execute('SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id=? AND subject_id=? AND currency='+ACTIVE_NAV_CURRENCY_SQL,(web.TENANT_ID,sid)).fetchone()['latest']
        context=AgentToolContext(web.TENANT_ID,'upgrade-cycle-acceptance',frozenset({sid}),'read-only-isolated-cycle')
        for tool in ('get_risk_metrics','compare_with_benchmarks'):
            value=execute_tool(portable,context,tool,{'subject_id':sid})
            data=value.get('data') or {};metrics=data.get('metrics') or {}
            missing=[k for k in required if not isinstance(metrics.get(k),(int,float)) or not math.isfinite(metrics[k])] if tool=='get_risk_metrics' else []
            last,required_day=None,None
            if tool=='compare_with_benchmarks':
                series=data.get('series') or []
                last=series[-1]['date'] if series else None
                required_day=benchmark_session_on_or_before(data['benchmark']['benchmark_id'],date.fromisoformat(str(latest))).isoformat()
            records.append({'subject':sid,'tool':tool,'latest_nav':str(latest),'as_of':value.get('as_of'),'quality':value.get('quality_status'),'missing_metrics':missing,'benchmark_series_latest':last,'benchmark_required_session':required_day,'data_keys':list(data),'status':data.get('status'),'cache_status':data.get('cache_status')})
    proxy=[dict(r) for r in db.execute("SELECT subject_id,holdings_as_of,conid,source_symbol,mapping_method,proxy_reason FROM crisis_replay_components WHERE tenant_id=%s AND subject_id='U16088571' AND holdings_as_of='2026-10-08' AND source_symbol='SPY'",(web.TENANT_ID,)).fetchall()]
    versions=[dict(r) for r in db.execute("SELECT subject_id,payload_json->>'runtime_version' AS runtime_version,payload_json->>'calculation_version' AS calculation_version,as_of_date,quality_status FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id IN ('alpha-world-ms','alpha-world-t') AND as_of_date='2026-10-08' ORDER BY subject_id,analytics_version",(web.TENANT_ID,)).fetchall()]
failed=[r for r in records if r['quality']=='fail' or r['missing_metrics'] or r['as_of']!=r['latest_nav'] or r['benchmark_series_latest']!=r['benchmark_required_session']]
result={'passed':not differences and not failed,'database_writes':0,'isolated_recovery_only':True,'protected_table_differences':differences,'protected_tables':clone,'records':records,'failed_consumers':failed,'spy_proxy_components':proxy,'current_runtime_versions':versions,'real_viewer_acceptance':False}
raw=canonical_json(result).encode();digest=hashlib.sha256(raw).hexdigest();name='review/platform-upgrade-20261009/cycle-acceptance-'+digest+'.json'
blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
blob.upload_blob(raw,overwrite=False)
if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Evidence differs')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':result['passed'],'database_writes':0,'protected_table_differences':differences,'records':records,'current_runtime_versions':versions,'private_blob':name,'sha256':digest},default=str),flush=True)
