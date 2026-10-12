"""Exercise installed HKD code in session-local PostgreSQL shadow tables.

The application role is not elevated. All recalculation writes are guarded to
pg_temp; public source/metadata/report tables are read only and fingerprinted.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.derived_portfolio import refresh_fixed_unit_composite
from ibkr_etl.risk_snapshots import build_risk_snapshot, snapshot_read_status
from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot, rebuild, factsheet_snapshot
from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload

TENANT='00000000-0000-0000-0000-000000000001'
PREFIX='review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
TABLES=('platform_subjects','subject_calculation_definitions','derived_portfolios',
    'derived_portfolio_nav','derived_portfolio_positions','derived_portfolio_exclusions',
    'derived_portfolio_cash_ledger','derived_portfolio_cash_balances',
    'synthetic_portfolio_components','risk_free_rates','risk_analytics_snapshots')


def sha(raw):return hashlib.sha256(raw).hexdigest()


class TemporaryOnlyConnection(PostgresConnection):
    def execute(self,sql,parameters=None):
        match=re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)',sql,re.I)
        if match:
            table=match.group(1)
            if table not in TABLES:raise ValueError('Mutation outside temporary rehearsal allowlist')
            row=self._connection.execute("SELECT n.nspname LIKE 'pg_temp_%%' AS temporary FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)",(table,)).fetchone()
            if not row or not row['temporary']:raise ValueError('Mutation would target persistent data')
        elif re.match(r'\s*(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b',sql,re.I):
            raise ValueError('Application DDL is forbidden in this rehearsal')
        return super().execute(sql,parameters)


def main():
    release=json.loads(Path('/app/ALPHA_MS_HKD_RELEASE.json').read_bytes())
    for name,digest in release['installed_files_sha256'].items():
        if sha(Path('/app',name).read_bytes())!=digest:raise ValueError('Installed candidate differs')
    storage=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    fixings_raw=storage.get_blob_client('internal-reports','review/alpha-world-ms/hkd-hibor-v14r8-20261007/inputs/official-hibor.json').download_blob().readall()
    if sha(fixings_raw)!=release['inputs_sha256']['official-hibor.json']:raise ValueError('Licensed fixing input differs')
    fixings=json.loads(fixings_raw)
    # The qualified archived capture is an oracle, not live calculation input.
    # Read it privately from storage rather than putting portfolio data in args.
    source_raw=storage.get_blob_client('internal-reports','review/alpha-world-ms/hkd-hibor-v14r8-20261007/inputs/source-inspection.json').download_blob().readall()
    if sha(source_raw)!=release['inputs_sha256']['source-inspection.json']:raise ValueError('Qualified recorded source differs')
    expected=rebuild(json.loads(source_raw))
    expected_snapshot=factsheet_snapshot(expected,fixings,'2026-09-30')
    if sha(json.dumps(expected_snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode())!=release['snapshot_sha256']['2026-09-30']:
        raise ValueError('Recorded oracle differs from approved HKD review')
    expected_rows=expected['series']
    if expected['contract_sha256']!=release['contract_sha256']:raise ValueError('Expected contract differs')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,
            options='-c statement_timeout=120000',connect_timeout=25) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
        role=dict(raw.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Non-administrator application role required')
        def fingerprint():
            queries={
                'metadata':"SELECT * FROM public.platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY subject_id",
                'contract':"SELECT * FROM public.subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY definition_version",
                'nav':"SELECT * FROM public.derived_portfolio_nav WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' ORDER BY snapshot_date,currency",
                'components':"SELECT * FROM public.synthetic_portfolio_components WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' ORDER BY snapshot_date,component_id",
                'reports':"SELECT snapshot_id,data_sha256,status FROM public.reporting_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY snapshot_id",
                'broker_nav':"SELECT row_sha256 FROM public.nav_snapshots WHERE account_id IN (SELECT account_id FROM public.accounts WHERE tenant_id=%s AND account_id IN ('U16088571','U16088606')) ORDER BY account_id,snapshot_date,currency,asset_class",
                'broker_fx':"SELECT row_sha256 FROM public.fx_rates WHERE account_id IN (SELECT account_id FROM public.accounts WHERE tenant_id=%s AND account_id IN ('U16088571','U16088606')) ORDER BY account_id,rate_date,from_currency,to_currency",
            }
            return {k:sha(json.dumps([dict(r) for r in raw.execute(sql,(TENANT,))],sort_keys=True,default=str).encode()) for k,sql in queries.items()}
        before=fingerprint()
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",('11111111-1111-1111-1111-111111111111',))
        if raw.execute("SELECT count(*) AS count FROM public.platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-ms'",(TENANT,)).fetchone()['count']!=0:
            raise ValueError('Production tenant boundary failed')
        raw.rollback()
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,));raw.commit()
        # LIKE INCLUDING ALL preserves PostgreSQL types/defaults/checks/indexes,
        # but does not copy rows, foreign keys or policies into a public schema.
        for table in TABLES:
            raw.execute('CREATE TEMP TABLE '+table+' (LIKE public.'+table+' INCLUDING ALL)')
            raw.execute('INSERT INTO pg_temp.'+table+' SELECT * FROM public.'+table+' WHERE tenant_id=%s',(TENANT,))
        raw.execute("CREATE TEMP VIEW subject_nav_series AS SELECT tenant_id,portfolio_id AS subject_id,'derived_portfolio'::text AS source_kind,snapshot_date,currency,total_nav::text AS total_text FROM pg_temp.derived_portfolio_nav")
        raw.commit()
        c=TemporaryOnlyConnection(raw)
        contract=approved_reference_definitions()['alpha-world-ms']
        c.execute("UPDATE platform_subjects SET base_currency='HKD',reporting_currency='HKD' WHERE tenant_id=? AND subject_id='alpha-world-ms'",(TENANT,))
        c.execute("UPDATE derived_portfolios SET base_currency='HKD' WHERE tenant_id=? AND portfolio_id='alpha-world-ms'",(TENANT,))
        c.execute("UPDATE subject_calculation_definitions SET configuration_json=?::jsonb,configuration_sha256=? WHERE tenant_id=? AND subject_id='alpha-world-ms' AND approval_status='approved' AND effective_to IS NULL",(json.dumps(contract),configuration_sha256(contract),TENANT))
        for r in fixings:
            c.execute("INSERT INTO risk_free_rates(tenant_id,rate_date,series_id,currency,tenor,annual_rate,day_count_convention,provider,fetched_at,quality_status) VALUES (?,?,'HKAB-HIBOR-1M','HKD','1-month',?,'annualized-money-market-fixing','licensed protected historical bootstrap','2026-10-07','pass') ON CONFLICT(tenant_id,rate_date,series_id) DO NOTHING",(TENANT,r['rate_date'],r['annual_rate']))
        c.commit()
        require_ms_hkd_contract(c,tenant_id=TENANT)
        result=refresh_fixed_unit_composite(c,contract)
        actual=[dict(r) for r in c.execute("SELECT snapshot_date,total_nav FROM derived_portfolio_nav WHERE tenant_id=? AND portfolio_id='alpha-world-ms' AND currency='HKD' AND snapshot_date<='2026-09-30' ORDER BY snapshot_date",(TENANT,))]
        if len(actual)!=len(expected_rows) or len(actual)!=443:raise ValueError('Historical NAV coverage differs')
        for a,b in zip(actual,expected_rows):
            if str(a['snapshot_date'])!=b['date'] or not math.isclose(float(a['total_nav']),b['nav'],rel_tol=1e-12,abs_tol=1e-10):
                raise ValueError('Actual PostgreSQL interpreter differs from approved HKD history')
        built=build_risk_snapshot(c,tenant_id=TENANT,subject_id='alpha-world-ms',as_of='2026-09-30',only_if_changed=True)
        replay=build_risk_snapshot(c,tenant_id=TENANT,subject_id='alpha-world-ms',as_of='2026-09-30',only_if_changed=True)
        if replay['status']!='unchanged':raise ValueError('PostgreSQL cache replay differs')
        row=c.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id='alpha-world-ms' AND as_of_date='2026-09-30' ORDER BY generated_at DESC LIMIT 1",(TENANT,)).fetchone()
        if snapshot_read_status(dict(row),subject_id='alpha-world-ms',as_of='2026-09-30')!='current':raise ValueError('Actual PostgreSQL cache guard failed')
        _,metrics,evidence=load_risk_snapshot(c,'2026-09-30',tenant_id=TENANT)
        tools=_collect_tool_results(c,tenant_id=TENANT,subject_id='alpha-world-ms',
            period_end='2026-09-30',reporting_currency='HKD')
        assembled,quality,publication,blockers=assemble_month_end_payload(subject_id='alpha-world-ms',
            period_start='2025-01-20',period_end='2026-09-30',reporting_currency='HKD',tool_results=tools)
        if quality=='fail' or publication or assembled['risk']['metrics']!=metrics:
            raise ValueError('Actual future month-end assembly failed or bypassed release controls')
        if not (assembled.get('monthly_reporting') or {}).get('available'):
            raise ValueError('Future package attribution is unavailable')
        c.commit()
        after=fingerprint()
        if before!=after:raise ValueError('Persistent source, metadata or report fingerprints changed')
        proof={'status':'passed','image_digest':os.environ['ALPHA_MS_IMAGE_DIGEST'],
            'role':role,'tenant_isolation':'pass','temporary_write_guard':'pass',
            'postgres_types_constraints_and_upserts':'pass','navs_verified':len(actual),
            'expected_recorded_source_sha256':sha(source_raw),'derived_result':result,
            'risk_build_status':built['status'],'risk_replay_status':replay['status'],
            'metrics':metrics,'analytics_evidence':evidence,'persistent_before':before,'persistent_after':after,
            'future_month_end_assembly':'pass','future_publication_allowed':publication,
            'future_publication_blockers':list(blockers),'monthly_reporting_available':True,
            'database_persistent_writes':0,'ibkr_source_changed':False,'reports_released':False,
            'isolated_database':'session-local PostgreSQL temporary shadow tables; not a separate server'}
        raw.rollback()
    content=json.dumps(proof,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    proof_name=PREFIX+'postgres-qualification-'+os.environ['ALPHA_MS_IMAGE_DIGEST'].split(':')[1]+'.json'
    blob=storage.get_blob_client('internal-reports',proof_name)
    blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':sha(content)})
    if blob.download_blob().readall()!=content:raise ValueError('Private PostgreSQL evidence readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','blob':proof_name,'sha256':sha(content),'navs_verified':len(actual),'database_persistent_writes':0}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,ValueError) else
                str(error.diag.message_primary) if isinstance(error,psycopg.ProgrammingError) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
