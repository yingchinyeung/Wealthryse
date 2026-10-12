"""Actual PostgreSQL temporary-table rehearsal; public data are fingerprinted.

Source price/FX inputs are frozen private captures, not outbound downloads.
No report rendering, approval, activation or persistent database write occurs.
"""
import hashlib
import json
import math
import os
from datetime import date, timedelta
import re
from contextlib import contextmanager
from unittest.mock import patch

import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.provisional_benchmark import refresh_and_materialize
from ibkr_etl.website_benchmarks import MS_HKD_COMPARISON_VERSION
from ibkr_etl import web, provisional_benchmark as provisional

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'
TABLES=('portfolio_benchmark_mapping','benchmark_master','benchmark_prices',
        'reference_fx_rates','performance_comparison_daily','data_quality_results')


def sha(content): return hashlib.sha256(content).hexdigest()


class TemporaryOnlyConnection(PostgresConnection):
    def execute(self,sql,parameters=None):
        match=re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)',sql,re.I)
        if match:
            table=match.group(1)
            if table not in TABLES: raise ValueError('Mutation outside comparison rehearsal allowlist')
            row=self._connection.execute("SELECT n.nspname LIKE 'pg_temp_%%' AS temporary FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)",(table,)).fetchone()
            if not row or not row['temporary']: raise ValueError('Mutation would target persistent data')
        elif re.match(r'\s*(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b',sql,re.I):
            raise ValueError('Application DDL prohibited')
        return super().execute(sql,parameters)


class Frame:
    def __init__(self, rows): self.rows=rows
    def iterrows(self):
        for day,value in self.rows: yield date.fromisoformat(day), {'Close':value}


class Ticker:
    def __init__(self, symbol, sources): self.symbol=symbol; self.sources=sources
    def history(self,**kwargs):
        if kwargs['interval']!='1d' or kwargs['auto_adjust'] is not False or kwargs['repair'] is not False:
            raise ValueError('Unexpected price convention')
        return Frame([(d,v) for d,v in self.sources[self.symbol] if kwargs['start']<=d<kwargs['end']])


def independent_audit(result, inputs):
    nav={str(r['snapshot_date']):float(r['total_nav']) for r in inputs['nav']}
    fx={r['rate_date']:float(r['rate']) for r in inputs['fx']
        if r['from_currency']=='USD' and r['to_currency']=='HKD'}
    if len(fx)!=len(inputs['fx'])//2: raise ValueError('Ambiguous FX input')
    summaries=[]
    if inputs['external_flows']: raise ValueError('Independent zero-flow replay must not ignore external flows')
    for item in result['benchmarks']:
        benchmark_id=item['benchmark_id']; rows=item['rows']
        levels={r['observation_date']:float(r['level']) for r in inputs['prices'] if r['benchmark_id']==benchmark_id}
        eligible=sorted(set(nav)&set(levels))
        if [r['observation_date'] for r in rows] != eligible: raise ValueError('Common-date coverage differs')
        expected=[]
        for day in eligible:
            factor=1.0
            if benchmark_id=='GSPC':
                candidates=[d for d in fx if d<=day]
                if not candidates: raise ValueError('No prior FX')
                fixing=max(candidates)
                if (date.fromisoformat(day)-date.fromisoformat(fixing)).days>4: raise ValueError('Stale FX')
                factor=fx[fixing]
            expected.append(levels[day]*factor)
        for i,(row,day,level) in enumerate(zip(rows,eligible,expected)):
            oracle={'portfolio_value':nav[day],'benchmark_level':level,
                    'normalized_portfolio':100*nav[day]/nav[eligible[0]],
                    'normalized_benchmark':100*level/expected[0]}
            if i:
                oracle.update(portfolio_return=nav[day]/nav[eligible[i-1]]-1,
                              benchmark_return=level/expected[i-1]-1,
                              excess_return=(nav[day]/nav[eligible[i-1]]-level/expected[i-1]))
            else:
                if any(row[k] is not None for k in ('portfolio_return','benchmark_return','excess_return')):
                    raise ValueError('First common observation must not have a return')
            for k,v in oracle.items():
                if not math.isclose(float(row[k]),v,rel_tol=1e-11,abs_tol=1e-10): raise ValueError('Independent comparison mismatch: '+k)
            if row['reporting_currency']!='HKD' or row['calculation_version']!=MS_HKD_COMPARISON_VERSION:
                raise ValueError('Currency/version mismatch')
        summaries.append({'benchmark_id':benchmark_id,'observations':len(rows),
                          'first_date':eligible[0],'last_date':eligible[-1],
                          'portfolio_total_return':nav[eligible[-1]]/nav[eligible[0]]-1,
                          'benchmark_total_return':expected[-1]/expected[0]-1,
                          'row_fields_independently_verified':8})
    return summaries


def main():
    storage=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True))
    content=storage.get_blob_client('internal-reports',BINDING['input_blob']).download_blob().readall()
    if sha(content)!=BINDING['inputs_sha256']: raise ValueError('Frozen input integrity differs')
    inputs=json.loads(content)
    sources={symbol:sorted((r['observation_date'],float(r['level'])) for r in inputs['prices']
                            if r['benchmark_id']==benchmark_id and r['observation_status']=='provisional_close')
             for symbol,benchmark_id in (('^GSPC','GSPC'),('^HSI','HSI'))}
    sources['HKD=X']=sorted((r['rate_date'],float(r['rate'])) for r in inputs['fx']
        if r['from_currency']=='USD' and r['to_currency']=='HKD' and r['provider']=='Yahoo Finance via yfinance')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,
            options='-c default_transaction_read_only=on -c statement_timeout=120000',connect_timeout=25) as raw:
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
        role=dict(raw.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
        if role['rolsuper'] or role['rolbypassrls']: raise ValueError('Non-administrator identity required')
        fingerprint_queries={
            'mappings':"SELECT * FROM public.portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY benchmark_id,effective_from",
            'comparisons':"SELECT * FROM public.performance_comparison_daily WHERE tenant_id=%s ORDER BY subject_id,benchmark_id,observation_date,calculation_version",
            'prices':"SELECT * FROM public.benchmark_prices WHERE tenant_id=%s AND benchmark_id IN ('GSPC','HSI') ORDER BY benchmark_id,observation_date,observation_status",
            'fx':"SELECT * FROM public.reference_fx_rates WHERE tenant_id=%s ORDER BY rate_date,from_currency,to_currency,provider",
            'subject':"SELECT * FROM public.platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-ms'",
            'contracts':"SELECT * FROM public.subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY definition_version",
            'nav':"SELECT * FROM public.derived_portfolio_nav WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' ORDER BY snapshot_date,currency",
            'snapshots':"SELECT * FROM public.reporting_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY snapshot_id",
            'assets':"SELECT * FROM public.subject_report_template_assets WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY template_asset_id",
            'batches':"SELECT * FROM public.onboarding_historical_report_batches WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY batch_id",
            'items':"SELECT i.* FROM public.onboarding_historical_report_items i JOIN public.onboarding_historical_report_batches b ON b.tenant_id=i.tenant_id AND b.batch_id=i.batch_id WHERE i.tenant_id=%s AND b.subject_id='alpha-world-ms' ORDER BY i.batch_id,i.period_end,i.report_type",
            'broker_nav':"SELECT row_sha256 FROM public.nav_snapshots WHERE account_id IN (SELECT account_id FROM public.accounts WHERE tenant_id=%s AND account_id IN ('U16088571','U16088606')) ORDER BY account_id,snapshot_date,currency,asset_class",
            'broker_fx':"SELECT row_sha256 FROM public.fx_rates WHERE account_id IN (SELECT account_id FROM public.accounts WHERE tenant_id=%s AND account_id IN ('U16088571','U16088606')) ORDER BY account_id,rate_date,from_currency,to_currency",
        }
        def fingerprint():
            return {k:sha(json.dumps([dict(r) for r in raw.execute(sql,(TENANT,))],sort_keys=True,default=str).encode())
                    for k,sql in fingerprint_queries.items()}
        before=fingerprint(); raw.commit()
        # A fixed setup transaction creates only pg_temp relations. Application
        # transactions then return to READ ONLY, which permits own-temp DML.
        raw.execute('SET TRANSACTION READ WRITE')
        for table in TABLES:
            raw.execute('CREATE TEMP TABLE '+table+' (LIKE public.'+table+' INCLUDING ALL)')
        # LIKE copies BIGSERIAL defaults referencing a persistent sequence.
        # Replace that default with a private sequence before application DML.
        raw.execute('CREATE TEMP SEQUENCE ms_review_quality_result_id')
        raw.execute("ALTER TABLE pg_temp.data_quality_results ALTER COLUMN quality_result_id SET DEFAULT nextval('pg_temp.ms_review_quality_result_id')")
        for table,clause in (
                ('portfolio_benchmark_mapping',"subject_id='alpha-world-ms'"),
                ('benchmark_master',"benchmark_id IN ('GSPC','HSI')"),
                ('benchmark_prices',"benchmark_id IN ('GSPC','HSI')"),
                ('reference_fx_rates',"(from_currency='USD' AND to_currency='HKD') OR (from_currency='HKD' AND to_currency='USD')"),
                ('performance_comparison_daily',"subject_id='alpha-world-ms'")):
            raw.execute('INSERT INTO pg_temp.'+table+' SELECT * FROM public.'+table+' WHERE tenant_id=%s AND ('+clause+')',(TENANT,))
        raw.execute('CREATE TEMP VIEW subject_nav_series AS SELECT tenant_id,portfolio_id AS subject_id,snapshot_date,currency,total_nav::text AS total_text FROM public.derived_portfolio_nav')
        # Preserve old mapping rows; apply the owner-requested pair only in pg_temp.
        today=raw.execute('SELECT CURRENT_DATE::text AS observed_date').fetchone()['observed_date']
        raw.execute("UPDATE pg_temp.portfolio_benchmark_mapping SET effective_to=%s WHERE tenant_id=%s AND subject_id=%s AND effective_to IS NULL",((date.fromisoformat(today)-timedelta(days=1)).isoformat(),TENANT,SUBJECT))
        for benchmark,kind in (('GSPC','primary'),('HSI','secondary')):
            raw.execute("INSERT INTO pg_temp.portfolio_benchmark_mapping(tenant_id,subject_id,benchmark_id,mapping_type,effective_from,reporting_currency,created_at) VALUES(%s,%s,%s,%s,%s,'HKD',CURRENT_TIMESTAMP::text)",(TENANT,SUBJECT,benchmark,kind,today))
        raw.commit()
        raw.execute('SET TRANSACTION READ ONLY')
        old_rows=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.performance_comparison_daily WHERE calculation_version='benchmark-comparison-v1' ORDER BY benchmark_id,observation_date")]
        c=TemporaryOnlyConnection(raw)
        # Fail after GSPC has written its rows, not only at the fetch stage.
        # The caller may subsequently commit its run-failure audit record.
        # All six temporary data tables must still be byte-equivalent.
        def temporary_fingerprint():
            return {table:sha(json.dumps(sorted(
                [json.dumps(dict(r),sort_keys=True,default=str) for r in raw.execute('SELECT * FROM pg_temp.'+table)]),
                sort_keys=True).encode()) for table in TABLES}
        temporary_before=temporary_fingerprint()
        real_build=provisional.build_comparison
        def failed_secondary(**kwargs):
            if kwargs['benchmark'].benchmark_id=='HSI':
                raise RuntimeError('Injected secondary comparison validation failure')
            return real_build(**kwargs)
        with patch.object(provisional,'build_comparison',failed_secondary):
            try:
                refresh_and_materialize(c,account_id=SUBJECT,start_date=date(2025,1,20),
                    end_date=date.fromisoformat(max(r['snapshot_date'] for r in inputs['nav'])),
                    ticker_factory=lambda symbol:Ticker(symbol,sources))
            except RuntimeError as error:
                if str(error)!='Injected secondary comparison validation failure': raise
            else: raise ValueError('Failure injection did not execute')
        raw.commit()
        if temporary_fingerprint()!=temporary_before:
            raise ValueError('Failed refresh committed a partial benchmark pair')
        result=refresh_and_materialize(c,account_id=SUBJECT,start_date=date(2025,1,20),
            end_date=date.fromisoformat(max(r['snapshot_date'] for r in inputs['nav'])),
            ticker_factory=lambda symbol:Ticker(symbol,sources))
        for item in result['benchmarks']:
            item['rows']=[dict(r) for r in raw.execute('SELECT * FROM pg_temp.performance_comparison_daily WHERE benchmark_id=%s AND calculation_version=%s ORDER BY observation_date',(item['benchmark_id'],MS_HKD_COMPARISON_VERSION))]
        audit=independent_audit(result,inputs)
        retained=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.performance_comparison_daily WHERE calculation_version='benchmark-comparison-v1' ORDER BY benchmark_id,observation_date")]
        if retained!=old_rows: raise ValueError('Legacy USD comparisons changed')
        payload=web._onboarding_preview_payload(raw,SUBJECT)
        if not payload['validation']['ready_for_owner_preview_approval']:
            raise ValueError('Temporary website preview blocked: '+str([r['check_id'] for r in payload['validation']['results'] if r['status']=='fail']))
        @contextmanager
        def isolated_database(): yield raw
        endpoints=[]
        with patch.object(web,'_database',isolated_database),patch.object(web,'_portfolio_access_context',return_value={'reporting_currency':'HKD','preview_mode':True}):
            for benchmark in ('GSPC','HSI'):
                response=web.benchmark_comparison(SUBJECT,benchmark_id=benchmark,as_of=payload['nav']['latest_date'],user={'role':'owner','email':'owner-fixture'})
                if not response['available'] or response['reporting_currency']!='HKD' or response['calculation_version']!=MS_HKD_COMPARISON_VERSION:
                    raise ValueError('Actual website reader currency/version mismatch')
                endpoints.append({'benchmark_id':benchmark,'observations':len(response['series']),
                    'reporting_currency':response['reporting_currency'],'metrics':response['metrics']})
        raw.rollback()
        after=fingerprint()
        if before!=after: raise ValueError('Public data changed during private rehearsal')
        raw.rollback()
    proof={'status':'temporary_postgres_and_website_readers_passed','source_binding':BINDING,
        'audit':audit,'endpoints':endpoints,'preview_validation':payload['validation'],
        'historical_batch_status':payload['historical_reports']['batch']['status'],
        'public_fingerprints_before':before,'public_fingerprints_after':after,
        'legacy_usd_rows_unchanged':True,'database_persistent_writes':0,'factsheets_changed':False,
        'secondary_failure_rollback_verified':True,
        'ibkr_source_changed':False,'reports_released':False,'onboarding_activated':False,
        'authorization_test_scope':'Endpoint reads use an isolated owner fixture; live authorization is not bypassed'}
    content=json.dumps(proof,sort_keys=True,default=str,allow_nan=False).encode()
    blob=storage.get_blob_client('internal-reports',BINDING['output_blob'])
    blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':sha(content)})
    if blob.download_blob().readall()!=content: raise ValueError('Private qualification readback differs')
    print('MS_WEBSITE_REHEARSAL '+json.dumps({'status':proof['status'],'sha256':sha(content),
        'observations':sum(r['observations'] for r in audit),'database_persistent_writes':0}),flush=True)


if __name__=='__main__': main()
