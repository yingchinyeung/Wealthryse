"""Real changed daily branches, temporary SQL gap, private clone only."""
import os,sys,json,hashlib
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg import sql

source='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
target='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
p=urlsplit(os.environ['DATABASE_URL'])
if p.hostname!=source:raise ValueError('Unexpected credential source')
userinfo,sep,_=p.netloc.rpartition('@')
if not sep:raise ValueError('Existing credential authority required')
os.environ['DATABASE_URL']=urlunsplit((p.scheme,userinfo+'@'+target+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))
os.environ['IBKR_RUN_MIGRATIONS']='false'
original_connect=psycopg.connect
def only_clone(conninfo='',**kwargs):
    if conninfo_to_dict(conninfo,**kwargs).get('host')!=target:raise ValueError('No connection outside private clone')
    return original_connect(conninfo,**kwargs)
psycopg.connect=only_clone
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import connect
import run_self_service_derived_refresh as entry
TENANT=os.environ['PORTFOLIO_TENANT_ID']
if TENANT!='00000000-0000-0000-0000-000000000001':raise ValueError('Exact tenant required')
for name,digest in EXPECTED_FILES.items():
    if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Candidate module differs')
SHADOW=('performance_comparison_daily','reference_fx_rates','benchmark_master','benchmark_prices','data_quality_results','risk_analytics_snapshots')
PROTECTED=('accounts','nav_snapshots','position_snapshots','cash_snapshots','cash_transactions','trades','corporate_actions','fx_rates','flex_rows','subject_calculation_definitions','report_artifact_releases','report_release_decisions','reporting_snapshots','report_generation_runs',*SHADOW)
def fingerprints():
    result={}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=20) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');db.execute("SET LOCAL TIME ZONE 'UTC'");db.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        for name in (*PROTECTED,'subject_nav_series'):
            h=hashlib.sha256();count=0
            with db.cursor(name='private_derived_preservation') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS h FROM {} t ORDER BY 1').format(sql.Identifier('public',name)))
                for row in cursor:h.update((row['h']+'\n').encode());count+=1
            result[name]={'rows':count,'sha256':h.hexdigest()}
    return result
before=fingerprints();records=[]
connection=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],TENANT)
try:
    for name in SHADOW:
        # Identifiers come only from the fixed literal tuple, never user data.
        connection.execute('CREATE TEMP TABLE '+name+' (LIKE public.'+name+' INCLUDING ALL) ON COMMIT PRESERVE ROWS')
        if name!='data_quality_results':connection.execute('INSERT INTO pg_temp.'+name+' OVERRIDING SYSTEM VALUE SELECT * FROM public.'+name)
    connection.commit()
    removed=connection.execute("DELETE FROM pg_temp.performance_comparison_daily WHERE tenant_id=? AND subject_id IN ('alpha-world-ms','alpha-world-t') AND observation_date='2026-10-08' AND calculation_version IN ('benchmark-comparison-v2-ms-hkd','benchmark-comparison-v2-t-hkd') RETURNING subject_id,benchmark_id",(TENANT,)).fetchall()
    if len(removed)!=4:raise ValueError('Exactly four temporary comparison gaps required')
    connection.commit()
    for subject in ('alpha-world-ms','alpha-world-t'):
        version='benchmark-comparison-v2-ms-hkd' if subject.endswith('-ms') else 'benchmark-comparison-v2-t-hkd'
        prior=connection.execute('SELECT MAX(observation_date) AS latest FROM performance_comparison_daily WHERE tenant_id=? AND subject_id=? AND calculation_version=?',(TENANT,subject,version)).fetchone()['latest']
        if str(prior)!='2026-10-07':raise ValueError('Controlled temporary previous-day gap not established')
        result=entry._refresh_analytics_if_approved(connection,{'subject_id':subject},tenant_id=TENANT)
        benchmark=result['benchmark_comparison']
        rows=connection.execute('SELECT benchmark_id,MAX(observation_date) AS latest FROM performance_comparison_daily WHERE tenant_id=? AND subject_id=? AND calculation_version=? GROUP BY benchmark_id ORDER BY benchmark_id',(TENANT,subject,version)).fetchall()
        if result['onboarding_activated'] or result['acceptance_check_changed'] or not benchmark['available'] or not benchmark['refreshed_after_validated_nav'] or len(rows)!=2 or any(str(row['latest'])!='2026-10-08' for row in rows):raise ValueError('Changed daily branch did not repair both governed tails')
        records.append({'subject':subject,'before':str(prior),'after':[dict(r) for r in rows],'approved_pair_refreshed':True,'onboarding_activated':False})
    connection.commit()
finally:
    connection.rollback();connection.close()
after=fingerprints();differences=[name for name in before if before[name]!=after[name]]
proof={'passed':not differences and len(records)==2,'live_database_writes':0,'clone_public_table_differences':differences,'public_tables_checked':len(before),'session_temporary_rows_removed':len(removed),'session_temporary_tables_dropped':True,'records':records,'candidate_files':EXPECTED_FILES,'scope':'Actual changed daily branches with four temporary comparison gaps, real approved market-data paths and risk processing. No persistent clone table change; sequence values are not claimed unchanged.'}
print('DERIVED_TAIL_PROOF '+json.dumps(proof,default=str),flush=True)
if not proof['passed']:raise SystemExit(1)
