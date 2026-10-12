"""Owner-authorized, atomic MS-only cutover with protected rollback evidence.

The exact installed image is qualified separately. Rehearsal uses PostgreSQL
session-local shadow tables; application commit calls are deferred to the outer
transaction. No IBKR source table is an allowed mutation target.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
from datetime import datetime, timezone, date

sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.derived_portfolio import refresh_fixed_unit_composite
from ibkr_etl.risk_snapshots import build_risk_snapshot,snapshot_read_status
from ibkr_etl.alpha_world_ms_hkd import rebuild,factsheet_snapshot,load_risk_snapshot
from ibkr_etl.reporting_snapshot import freeze_month_end_snapshot
from ibkr_etl.hibor import refresh,latest_expected_fixing,load_period_average

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'
PREFIX='review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
INPUT='review/alpha-world-ms/hkd-hibor-v14r8-20261007/inputs/'
OLD_HASH='3c4a34a1bd8adec4b7884cb4f02b92750badc50c2d3014b299c3aa5e21a891f1'
NEW_HASH='a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc'
OWNER='a38491c3f0c09db172e0d34c83f0ab3de67011b1e082d2ea6a01e7804c0ffb54'
MUTABLE=('derived_portfolio_nav','synthetic_portfolio_components',
    'derived_portfolio_positions','derived_portfolio_exclusions',
    'derived_portfolio_cash_ledger','derived_portfolio_cash_balances','risk_analytics_snapshots')
TABLES=('platform_subjects','subject_calculation_definitions','derived_portfolios',
    'reporting_snapshots','risk_free_rates')+MUTABLE
MODE=os.environ.get('ALPHA_MS_CUTOVER_MODE','rehearse')
PHASE='start'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
def sha(value):return hashlib.sha256(value).hexdigest()


class AtomicScopedConnection(PostgresConnection):
    def __init__(self,raw,temporary):super().__init__(raw);self.temporary=temporary
    def execute(self,sql,parameters=None):
        match=re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)',sql,re.I)
        if match:
            table=match.group(1)
            if table not in TABLES:raise ValueError('Mutation outside MS cutover allowlist')
            namespace=self._connection.execute("SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)",(table,)).fetchone()[0]
            if (namespace.startswith('pg_temp_'))!=self.temporary:raise ValueError('Wrong mutation namespace')
            if not self.temporary and namespace!='public':raise ValueError('Unexpected persistent namespace')
        elif re.match(r'\s*(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b',sql,re.I):
            raise ValueError('Application DDL is forbidden')
        return super().execute(sql,parameters)
    def commit(self):pass
    def rollback(self):raise ValueError('Nested calculation failed; outer cutover must roll back')


def scope(table):
    if table=='risk_free_rates':return "tenant_id=? AND series_id='HKAB-HIBOR-1M'"
    field='portfolio_id' if table.startswith('derived_portfolio') or table=='synthetic_portfolio_components' else 'subject_id'
    return 'tenant_id=? AND '+field+"='alpha-world-ms'"


def backup(c):
    return {table:[dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE '+scope(table),(TENANT,))] for table in TABLES}


def data_hashes(tables):
    return {name:sha(canonical(sorted(rows,key=lambda r:canonical(r)))) for name,rows in tables.items()}


def protected_fingerprints(c):
    # Fingerprint actual source values and immutable report payloads, not counts.
    queries={
        'broker_nav':"SELECT * FROM public.nav_snapshots WHERE account_id IN ('U16088571','U16088606')",
        'broker_fx':"SELECT * FROM public.fx_rates WHERE account_id IN ('U16088571','U16088606')",
        'broker_positions':"SELECT * FROM public.position_snapshots WHERE account_id IN ('U16088571','U16088606')",
        'other_derived_nav':"SELECT * FROM public.derived_portfolio_nav WHERE tenant_id=? AND portfolio_id<>'alpha-world-ms'",
        'market_prices':"SELECT * FROM public.market_data_prices",
        'published_snapshots':"SELECT snapshot_id,data_sha256,snapshot_payload_json,status FROM public.reporting_snapshots WHERE tenant_id=? AND subject_id='alpha-world-ms' AND status='approved'",
        'operating_registration':"SELECT * FROM public.subject_operating_registrations WHERE tenant_id=? AND subject_id='alpha-world-ms'",
    }
    results={}
    for name,sql in queries.items():
        rows=[dict(r) for r in c.execute(sql,(TENANT,) if '?' in sql else None)]
        results[name]=sha(canonical(sorted(rows,key=lambda r:canonical(r))))
    return results


def restore(c,original):
    # Retain the new definition/snapshots as superseded audit history. Restore
    # prior currency, approved contract and every recalculable MS table exactly.
    c.execute("UPDATE subject_calculation_definitions SET approval_status='superseded',effective_to=effective_from WHERE "+scope('subject_calculation_definitions')+" AND configuration_sha256=? AND effective_to IS NULL",(TENANT,NEW_HASH))
    for row in original['subject_calculation_definitions']:
        c.execute('UPDATE subject_calculation_definitions SET approval_status=?,effective_to=? WHERE tenant_id=? AND definition_id=?',
            (row['approval_status'],row['effective_to'],TENANT,str(row['definition_id'])))
    row=original['platform_subjects'][0]
    c.execute('UPDATE platform_subjects SET base_currency=?,reporting_currency=? WHERE '+scope('platform_subjects'),(row['base_currency'],row['reporting_currency'],TENANT))
    row=original['derived_portfolios'][0]
    columns=[k for k in row if k not in ('tenant_id','portfolio_id')]
    c.execute('UPDATE derived_portfolios SET '+','.join(k+'=?' for k in columns)+' WHERE '+scope('derived_portfolios'),
        tuple(json.dumps(row[k]) if isinstance(row[k],(dict,list)) else row[k] for k in columns)+(TENANT,))
    for table in MUTABLE+('risk_free_rates',):
        c.execute('DELETE FROM '+table+' WHERE '+scope(table),(TENANT,))
        if original[table]:c.execute('INSERT INTO '+table+' SELECT * FROM jsonb_populate_recordset(NULL::'+table+',?::jsonb)',(canonical(original[table]).decode(),))
    ids={str(r['snapshot_id']) for r in original['reporting_snapshots']}
    for row in c.execute('SELECT snapshot_id,status FROM reporting_snapshots WHERE '+scope('reporting_snapshots'),(TENANT,)).fetchall():
        if str(row['snapshot_id']) not in ids:
            if row['status']!='frozen':raise ValueError('Rollback cannot alter a subsequently released snapshot')
            c.execute("UPDATE reporting_snapshots SET status='superseded' WHERE tenant_id=? AND snapshot_id=?",(TENANT,str(row['snapshot_id'])))
    for row in original['reporting_snapshots']:
        c.execute('UPDATE reporting_snapshots SET status=? WHERE tenant_id=? AND snapshot_id=?',(row['status'],TENANT,str(row['snapshot_id'])))


def qualify_restoration(c,original):
    after=backup(c)
    for table in MUTABLE+('risk_free_rates','derived_portfolios','platform_subjects'):
        if data_hashes({table:original[table]})!=data_hashes({table:after[table]}):raise ValueError('Restored data differs: '+table)
    for table,key in (('subject_calculation_definitions','definition_id'),('reporting_snapshots','snapshot_id')):
        ids={str(r[key]) for r in original[table]}
        selected=[r for r in after[table] if str(r[key]) in ids]
        if data_hashes({table:original[table]})!=data_hashes({table:selected}):raise ValueError('Retained original evidence differs: '+table)


def upgrade(c,original,fixings,periods,expected):
    global PHASE
    PHASE='versioned_contract'
    active=[r for r in original['subject_calculation_definitions'] if r['approval_status']=='approved' and r['effective_to'] is None]
    if len(active)!=1 or active[0]['configuration_sha256']!=OLD_HASH:raise ValueError('Exact previous approved USD contract required')
    old=active[0]
    if old['approved_by_identity_sha256']!=OWNER:raise ValueError('Previous owner attribution differs')
    subject=original['platform_subjects'][0]
    if subject['base_currency']!='USD' or subject['reporting_currency']!='USD' or subject['lifecycle_status']!='onboarding':raise ValueError('MS currency or onboarding state drift')
    contract=approved_reference_definitions()[SUBJECT]
    if configuration_sha256(contract)!=NEW_HASH:raise ValueError('Installed HKD contract differs')
    now=datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE subject_calculation_definitions SET approval_status='superseded',effective_to=effective_from WHERE tenant_id=? AND definition_id=? AND approval_status='approved' AND effective_to IS NULL",(TENANT,str(old['definition_id'])))
    definition=str(uuid.uuid5(uuid.NAMESPACE_URL,'wealthryse:alpha-ms-hkd:'+NEW_HASH))
    version=max(r['definition_version'] for r in original['subject_calculation_definitions'])+1
    c.execute("""INSERT INTO subject_calculation_definitions(definition_id,tenant_id,subject_id,subject_type,
        calculation_kind,definition_version,configuration_json,configuration_sha256,approval_status,effective_from,
        created_by_identity_sha256,approved_by_identity_sha256,created_at,approved_at)
        VALUES(?,?,?,?,?,?,?::jsonb,?,'approved',?,?,?,?,?)""",
        (definition,TENANT,SUBJECT,old['subject_type'],old['calculation_kind'],version,json.dumps(contract),NEW_HASH,
        old['effective_from'],OWNER,OWNER,now,now))
    c.execute("UPDATE platform_subjects SET base_currency='HKD',reporting_currency='HKD' WHERE "+scope('platform_subjects'),(TENANT,))
    c.execute("UPDATE derived_portfolios SET base_currency='HKD' WHERE "+scope('derived_portfolios'),(TENANT,))
    PHASE='licensed_hibor'
    for r in fixings:
        c.execute("""INSERT INTO risk_free_rates(tenant_id,rate_date,series_id,currency,tenor,annual_rate,
            day_count_convention,provider,fetched_at,quality_status)
            VALUES(?,?,'HKAB-HIBOR-1M','HKD','1-month',?,'annualized-money-market-fixing',?,?,'pass')
            ON CONFLICT(tenant_id,rate_date,series_id) DO NOTHING""",
            (TENANT,r['rate_date'],r['annual_rate'],'licensed protected HKAB/HKMA historical bootstrap',now))
    policy=latest_expected_fixing(now=datetime.now(timezone.utc))
    hibor=refresh(c,date(2025,1,20),date.fromisoformat(policy['expected_date']),license_scope_confirmed=True)
    PHASE='derived_hkd'
    require_ms_hkd_contract(c,tenant_id=TENANT)
    result=refresh_fixed_unit_composite(c,contract)
    actual=[dict(r) for r in c.execute("SELECT snapshot_date,total_nav FROM derived_portfolio_nav WHERE "+scope('derived_portfolio_nav')+" AND currency='HKD' AND snapshot_date<='2026-09-30' ORDER BY snapshot_date",(TENANT,))]
    if len(actual)!=443 or len(actual)!=len(expected['series']):raise ValueError('HKD history coverage differs')
    for a,b in zip(actual,expected['series']):
        if str(a['snapshot_date'])!=b['date'] or not math.isclose(float(a['total_nav']),b['nav'],rel_tol=1e-12,abs_tol=1e-10):raise ValueError('HKD live calculation differs from approved history')
    usd=[r for r in backup(c)['derived_portfolio_nav'] if r['currency']=='USD']
    oldusd=[r for r in original['derived_portfolio_nav'] if r['currency']=='USD']
    if data_hashes({'USD':usd})!=data_hashes({'USD':oldusd}):raise ValueError('Legacy USD NAV evidence changed')
    PHASE='risk_caches'
    dates=[str(r[0]) for r in c.execute("SELECT snapshot_date FROM derived_portfolio_nav WHERE "+scope('derived_portfolio_nav')+" AND currency='HKD' ORDER BY snapshot_date",(TENANT,))]
    built=[];withheld=[]
    from ibkr_etl.risk_snapshots import RiskInputsNotReady
    for day in dates:
        try:
            record=build_risk_snapshot(c,tenant_id=TENANT,subject_id=SUBJECT,as_of=day,only_if_changed=True)
            built.append({'date':day,'status':record['status']})
        except RiskInputsNotReady as error:
            # Only initial insufficient-history dates may be legitimately absent.
            if day>'2025-01-22':raise
            withheld.append({'date':day,'reason':str(error)})
    PHASE='historical_reporting_snapshots'
    reports=[]
    for period in periods:
        result_snapshot=freeze_month_end_snapshot(c,tenant_id=TENANT,subject_id=SUBJECT,
            period_start='2025-01-20',period_end=period,reporting_currency='HKD',reject_quality_fail=True)
        if result_snapshot.publication_allowed:raise ValueError('Unqualified comparisons must not be released')
        _,metrics,evidence=load_risk_snapshot(c,period,tenant_id=TENANT)
        expected_metrics=factsheet_snapshot(expected,fixings,period)['risk']['metrics']
        for name,value in expected_metrics.items():
            if value is None:
                if metrics.get(name) is not None:raise ValueError('Historical metric availability changed')
            elif not math.isclose(float(metrics[name]),float(value),rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Historical metric differs: '+period+'/'+name)
        reports.append({'period_end':period,'snapshot_id':result_snapshot.snapshot_id,
            'data_sha256':result_snapshot.data_sha256,'currency':'HKD','publication_allowed':False,
            'publication_blockers':list(result_snapshot.publication_blockers),'metrics':metrics})
    PHASE='latest_readback'
    _,latest_metrics,_=load_risk_snapshot(c,dates[-1],tenant_id=TENANT)
    if c.execute('SELECT lifecycle_status FROM platform_subjects WHERE '+scope('platform_subjects'),(TENANT,)).fetchone()[0]!='onboarding':raise ValueError('Onboarding activation forbidden')
    return {'definition_id':definition,'definition_version':version,'contract_sha256':NEW_HASH,
        'derived_result':result,'historical_navs_verified':443,'hibor':hibor,'hibor_policy':policy,
        'historical_risk_caches':len(built),'withheld_initial_dates':withheld,'reports':reports,
        'latest_metrics':latest_metrics,'legacy_usd_nav_unchanged':True}


def main():
    global PHASE
    if MODE not in ('rehearse','apply'):raise ValueError('Only explicit rehearsal/apply modes permitted')
    if os.environ.get('DEPLOYMENT_ENVIRONMENT','').lower()!='staging':raise ValueError('Expected deployment environment required')
    if os.environ.get('ALPHA_MS_OWNER_SHA256')!=OWNER:raise ValueError('Verified owner authorization required')
    release=json.loads(Path('/app/ALPHA_MS_HKD_RELEASE.json').read_bytes())
    image=os.environ['ALPHA_MS_IMAGE_DIGEST']
    for name,digest in release['installed_files_sha256'].items():
        if sha(Path('/app',name).read_bytes())!=digest:raise ValueError('Installed runtime differs')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def get(name):return service.get_blob_client('internal-reports',name).download_blob().readall()
    def put(name,content):
        blob=service.get_blob_client('internal-reports',PREFIX+name)
        blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':sha(content)})
        if blob.download_blob().readall()!=content:raise ValueError('Protected evidence readback differs')
        return PREFIX+name
    source=get(INPUT+'source-inspection.json');fixings_raw=get(INPUT+'official-hibor.json')
    if sha(source)!=release['inputs_sha256']['source-inspection.json'] or sha(fixings_raw)!=release['inputs_sha256']['official-hibor.json']:raise ValueError('Approved input hash differs')
    expected=rebuild(json.loads(source));fixings=json.loads(fixings_raw);periods=sorted(release['snapshot_sha256'])
    if MODE=='apply':
        rehearsal=json.loads(get(PREFIX+'atomic-rehearsal-'+image.split(':')[1]+'.json'))
        if rehearsal['status']!='passed' or rehearsal['rollback_rehearsal']!='passed' or rehearsal['image_digest']!=image:raise ValueError('Exact atomic/rollback rehearsal required')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c statement_timeout=180000 -c lock_timeout=15000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
        role=dict(raw.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Non-admin runtime role required')
        raw.commit()
        if MODE=='rehearse':
            for table in TABLES:
                raw.execute('CREATE TEMP TABLE '+table+' (LIKE public.'+table+' INCLUDING ALL)')
                raw.execute('INSERT INTO pg_temp.'+table+' SELECT * FROM public.'+table+' WHERE tenant_id=%s',(TENANT,))
            raw.execute("CREATE TEMP VIEW subject_nav_series AS SELECT tenant_id,portfolio_id AS subject_id,'derived_portfolio'::text AS source_kind,snapshot_date,currency,total_nav::text AS total_text FROM pg_temp.derived_portfolio_nav")
            raw.commit()
        raw.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        c=AtomicScopedConnection(raw,MODE=='rehearse')
        for key in ('ibkr-daily:','self-service-derived:','alpha-ms-hkd-cutover:'):
            if not c.execute('SELECT pg_try_advisory_xact_lock(hashtext(?))',(key+TENANT,)).fetchone()[0]:raise ValueError('Scheduled data processing is busy')
        before=protected_fingerprints(c);original=backup(c)
        if MODE=='apply':
            PHASE='protect_full_rollback_backup'
            backup_bytes=canonical({'tables':original,'fingerprints':before,'image_digest':image,'owner_identity_sha256':OWNER,
                'authorization':'Explicit owner request to replace Azure with HKD and period-average 1M HIBOR',
                'original_table_hashes':data_hashes(original),'source_data_mutation_authorized':False})
            backup_blob=put('rollback-backup-'+image.split(':')[1]+'.json',backup_bytes)
        result=upgrade(c,original,fixings,periods,expected)
        if protected_fingerprints(c)!=before:raise ValueError('Protected source, publication or operating state changed')
        if MODE=='rehearse':
            PHASE='rollback_rehearsal'
            restore(c,original);qualify_restoration(c,original)
            if protected_fingerprints(c)!=before:raise ValueError('Rollback touched protected inputs')
            raw.rollback()
            result.update({'status':'passed','rollback_rehearsal':'passed','database_persistent_writes':0})
        else:
            PHASE='protect_precommit_evidence'
            put('precommit-'+image.split(':')[1]+'.json',canonical(result))
            raw.commit()
            result.update({'status':'committed','backup_blob':backup_blob,'backup_sha256':sha(backup_bytes),'database_persistent_writes':'scoped MS derived/metadata/cache/report versions and licensed HIBOR only'})
        result.update({'mode':MODE,'image_digest':image,'owner_identity_sha256':OWNER,'role':role,
            'protected_fingerprints':before,'ibkr_source_changed':False,'reports_released':False,'onboarding_activated':False})
    PHASE='protect_completion'
    name=('atomic-rehearsal-' if MODE=='rehearse' else 'database-cutover-')+image.split(':')[1]+'.json'
    content=canonical(result);blob=put(name,content)
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':result['status'],'mode':MODE,'blob':blob,'sha256':sha(content),
        'historical_navs_verified':443,'historical_months':len(periods),'rollback_rehearsal':result.get('rollback_rehearsal')}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','mode':MODE,'phase':PHASE,
            'error_class':type(error).__name__,'error':str(error) if isinstance(error,ValueError) else
                str(error.diag.message_primary) if isinstance(error,psycopg.ProgrammingError) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
