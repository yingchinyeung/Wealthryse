"""T candidate rehearsal using PostgreSQL session-local tables only.

The application phase runs READ ONLY: PostgreSQL permits private temporary
DML, but rejects persistent writes even if the extra SQL guard were bypassed.
No subject, approval, report, membership or public object is created.
"""
import os,sys,json,hashlib,base64,zlib,types,re,uuid
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory

TENANT='00000000-0000-0000-0000-000000000001'
TABLES=('platform_subjects','subject_calculation_definitions','derived_portfolios',
 'derived_portfolio_nav','derived_portfolio_positions','derived_portfolio_exclusions',
 'derived_portfolio_cash_ledger','derived_portfolio_cash_balances','synthetic_portfolio_components',
 'risk_free_rates','risk_analytics_snapshots','account_onboarding_cases',
 'account_onboarding_approvals','account_onboarding_checks','benchmark_master',
 'benchmark_prices','portfolio_benchmark_mapping','performance_comparison_daily',
 'reference_fx_rates','data_quality_results')

def sha(raw):return hashlib.sha256(raw).hexdigest()

class TemporaryOnlyConnection(PostgresConnection):
    def execute(self,sql,parameters=None):
        match=re.match(r'\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([\w.]+)',sql,re.I)
        if match:
            table=match.group(1)
            if table not in TABLES:raise ValueError('Write outside temporary allowlist')
            row=self._connection.execute("SELECT n.nspname LIKE 'pg_temp_%%' AS temporary FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)",(table,)).fetchone()
            if not row or not row['temporary']:raise ValueError('Write would target persistent table')
        elif re.match(r'\s*(?:CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|DO)\b',sql,re.I):
            raise ValueError('Application DDL prohibited')
        return super().execute(sql,parameters)

def main():
    encoded=''.join(os.environ[f'T_QUALIFICATION_{i}'] for i in range(int(os.environ['T_QUALIFICATION_PARTS'])))
    bundle=json.loads(zlib.decompress(base64.b64decode(encoded)))
    if bundle.get('installed_candidate'):
        from pathlib import Path
        for name,digest in bundle['installed_files_sha256'].items():
            if sha((Path('/app')/name).read_bytes())!=digest:
                raise ValueError('Installed candidate differs from frozen release')
    import ibkr_etl
    # Ordered module list is bound to the local reviewed source hashes.
    for name,source in bundle['sources'].items():
        if sha(source.encode())!=bundle['hashes'][name]:raise ValueError('Candidate source binding differs')
        module=types.ModuleType('ibkr_etl.'+name);module.__package__='ibkr_etl'
        module.__file__='/app/src/ibkr_etl/'+name+'.py'
        sys.modules[module.__name__]=module
        exec(compile(source,module.__file__,'exec'),module.__dict__)
        setattr(ibkr_etl,name,module)
    from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256
    from ibkr_etl.derived_portfolio import refresh_fixed_unit_composite
    from ibkr_etl.alpha_world_t_analytics import require_contract,load_risk_snapshot
    from ibkr_etl.alpha_world_t_presentation import build_model
    from ibkr_etl.risk_snapshots import build_risk_snapshot,snapshot_read_status
    if not bundle.get('operating_candidate'):
        from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload
    contract=approved_reference_definitions()['alpha-world-t']
    results=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
        options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
        role=dict(raw.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone())
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Application identity required')
        protected=(*TABLES,'reporting_snapshots')
        def fingerprint():
            return {table:sha(json.dumps(sorted([dict(r) for r in raw.execute('SELECT * FROM public.'+table+' WHERE tenant_id=%s',(TENANT,))],
                key=lambda r:json.dumps(r,sort_keys=True,default=str)),sort_keys=True,default=str).encode()) for table in protected}
        before=fingerprint()
        state=[dict(r) for r in raw.execute("SELECT subject_id,lifecycle_status,base_currency,reporting_currency FROM public.platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-t'",(TENANT,))]
        if state!=bundle['expected_subject_state']:
            raise ValueError('T production state differs from reviewed onboarding case')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",('11111111-1111-1111-1111-111111111111',))
        if raw.execute('SELECT count(*) AS n FROM public.platform_subjects WHERE tenant_id=%s',(TENANT,)).fetchone()['n']:
            raise ValueError('Persistent tenant boundary failed')
        raw.rollback()
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,));raw.commit()
        raw.execute('SET TRANSACTION READ WRITE')
        for table in TABLES:
            raw.execute('CREATE TEMP TABLE '+table+' (LIKE public.'+table+' INCLUDING ALL)')
            private_sequences=[]
            # Prevent copied BIGSERIAL defaults from advancing public sequences.
            defaults=raw.execute("SELECT a.attname,pg_get_expr(d.adbin,d.adrelid) AS expr FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=to_regclass(%s)",(table,)).fetchall()
            for d in defaults:
                if 'nextval(' in d['expr']:
                    if not re.fullmatch(r'[a-z_]+',d['attname']):raise ValueError('Unexpected identity column')
                    sequence=table+'_'+d['attname']+'_private'
                    raw.execute('CREATE TEMP SEQUENCE '+sequence)
                    raw.execute('ALTER TABLE pg_temp.'+table+' ALTER COLUMN '+d['attname']+" SET DEFAULT nextval('pg_temp."+sequence+"')")
                    private_sequences.append((sequence,d['attname']))
            raw.execute('INSERT INTO pg_temp.'+table+' SELECT * FROM public.'+table+' WHERE tenant_id=%s',(TENANT,))
            for sequence,column in private_sequences:
                # Copied rows retain their IDs. Advance only the private
                # sequence past those IDs, never the persistent sequence.
                raw.execute("SELECT setval('pg_temp."+sequence+"',COALESCE(MAX("+column+"),0)+1,false) FROM pg_temp."+table)
        raw.execute("CREATE TEMP VIEW subject_nav_series AS SELECT tenant_id,subject_id,source_kind,snapshot_date,currency,total_text FROM public.subject_nav_series WHERE source_kind='physical_account' UNION ALL SELECT tenant_id,portfolio_id AS subject_id,'derived_portfolio'::text AS source_kind,snapshot_date,currency,total_nav::text AS total_text FROM pg_temp.derived_portfolio_nav")
        # The reviewed USD case is preserved in public. Replace only its private
        # calculation fixtures; this does not constitute an actual approval.
        raw.execute("DELETE FROM pg_temp.subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-t'",(TENANT,))
        raw.execute("DELETE FROM pg_temp.platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-t'",(TENANT,))
        raw.execute("INSERT INTO pg_temp.platform_subjects(tenant_id,subject_id,subject_type,display_name,base_currency,reporting_currency,history_start_date,inception_date,lifecycle_status) VALUES (%s,'alpha-world-t','composite_portfolio','Alpha World T','HKD','HKD','2025-01-20','2025-01-20','preview')",(TENANT,))
        raw.execute("INSERT INTO pg_temp.subject_calculation_definitions(definition_id,tenant_id,subject_id,subject_type,calculation_kind,definition_version,configuration_json,configuration_sha256,approval_status,effective_from,approved_at,approved_by_identity_sha256) VALUES (%s,%s,'alpha-world-t','composite_portfolio','fixed_unit_normalized_composite',1,%s::jsonb,%s,'approved','2025-01-20',now(),%s)",(str(uuid.uuid4()),TENANT,json.dumps(contract),configuration_sha256(contract),sha(b'isolated T rehearsal fixture - not a real owner approval')))
        raw.commit()
        if raw.execute('SHOW transaction_read_only').fetchone()[0]!='on':raise ValueError('Persistent DML guard is not enabled')
        raw.commit();c=TemporaryOnlyConnection(raw)
        rebuilt=refresh_fixed_unit_composite(c,contract)
        require_contract(c,tenant_id=TENANT)
        actual=[dict(r) for r in c.execute("SELECT snapshot_date,total_nav FROM derived_portfolio_nav WHERE tenant_id=? AND portfolio_id='alpha-world-t' AND currency='HKD' AND snapshot_date<='2026-09-30' ORDER BY snapshot_date",(TENANT,))]
        expected=bundle['nav']
        if len(actual)!=len(expected):raise ValueError('NAV observation count differs')
        for a,b in zip(actual,expected):
            if str(a['snapshot_date'])!=b['date'] or abs(float(a['total_nav'])-b['nav'])>1e-10:
                raise ValueError('Live PostgreSQL NAV differs from independently qualified history')
        for item in bundle['months']:
            period=item['period_end']
            built=build_risk_snapshot(c,tenant_id=TENANT,subject_id='alpha-world-t',as_of=period,only_if_changed=True)
            replay=build_risk_snapshot(c,tenant_id=TENANT,subject_id='alpha-world-t',as_of=period,only_if_changed=True)
            if replay['status']!='unchanged':raise ValueError('Temporary cache is not idempotent')
            row=dict(c.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id='alpha-world-t' AND as_of_date=?",(TENANT,period)).fetchone())
            if snapshot_read_status(row,subject_id='alpha-world-t',as_of=period)!='current':raise ValueError('Cache guard rejected PostgreSQL payload')
            if bundle.get('operating_candidate'):
                payload=row['payload_json']
                if isinstance(payload,str):payload=json.loads(payload)
                model={'risk':payload['metrics']}
            else:
                tools=_collect_tool_results(c,tenant_id=TENANT,subject_id='alpha-world-t',period_end=period,reporting_currency='HKD')
                snapshot,quality,allowed,blockers=assemble_month_end_payload(subject_id='alpha-world-t',period_start='2025-01-20',period_end=period,reporting_currency='HKD',tool_results=tools)
                model=build_model(snapshot)
                if quality!='warn' or allowed or 'risk:crisis_replay_unavailable' in blockers:raise ValueError('Internal release qualification differs')
            for key,value in item['metrics'].items():
                actual_value=model['risk'][key]
                if (value is None)!=(actual_value is None) or (value is not None and abs(actual_value-value)>1e-10):raise ValueError('Live risk metric differs '+key)
            results.append({'period_end':period,'cache':built['status'],'replay':replay['status'],
                'report_model':'not_exercised_operating_job' if bundle.get('operating_candidate') else 'passed','risk_fields':len(item['metrics'])})
        c.commit()
        onboarding=None
        if bundle.get('installed_candidate') and not bundle.get('operating_candidate'):
            from ibkr_etl.alpha_world_t_onboarding import refresh_onboarding_analytics
            before_check=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.account_onboarding_checks WHERE case_id IN (SELECT case_id FROM pg_temp.account_onboarding_cases WHERE target_subject_id='alpha-world-t') ORDER BY check_id")]
            onboarding=refresh_onboarding_analytics(c,tenant_id=TENANT)
            if onboarding['onboarding_activated'] or not onboarding['internal_shareholder_only']:
                raise ValueError('T acceptance scope differs')
            c.rollback()
            after_check=[dict(r) for r in raw.execute("SELECT * FROM pg_temp.account_onboarding_checks WHERE case_id IN (SELECT case_id FROM pg_temp.account_onboarding_cases WHERE target_subject_id='alpha-world-t') ORDER BY check_id")]
            if before_check!=after_check:raise ValueError('T onboarding acceptance rollback failed')
        operating_benchmark=None
        if bundle.get('operating_candidate')=='benchmark':
            from datetime import date
            from unittest.mock import patch
            from ibkr_etl import provisional_benchmark as bm
            latest=str(raw.execute("SELECT MAX(snapshot_date) AS latest FROM pg_temp.derived_portfolio_nav WHERE portfolio_id='alpha-world-t'").fetchone()['latest'])
            options={'account_id':'alpha-world-t','start_date':date(2025,1,20),'end_date':date.fromisoformat(latest)}
            comparison=bm.refresh_and_materialize(c,**options)
            if comparison['reporting_currency']!='HKD' or comparison['comparison_rows']<60:raise ValueError('T operating benchmark comparison failed')
            def private_comparison_state():
                return {table:sha(json.dumps(sorted([dict(r) for r in raw.execute('SELECT * FROM pg_temp.'+table)],
                    key=lambda r:json.dumps(r,sort_keys=True,default=str)),sort_keys=True,default=str).encode())
                    for table in ('performance_comparison_daily','benchmark_prices','reference_fx_rates','data_quality_results')}
            previous=private_comparison_state(); fetch=bm.fetch_benchmark_yfinance
            def fail_secondary(spec,*args,**kwargs):
                if spec.benchmark_id=='GSPC':raise RuntimeError('Intentional temporary-table secondary failure')
                return fetch(spec,*args,**kwargs)
            with patch.object(bm,'fetch_benchmark_yfinance',side_effect=fail_secondary):
                try:bm.refresh_and_materialize(c,**options)
                except RuntimeError as error:
                    if 'Required benchmark GSPC is unavailable' not in str(error):raise
                else:raise ValueError('Injected secondary failure did not reject pair')
            if previous!=private_comparison_state():raise ValueError('Partial benchmark writes survived rollback')
            operating_benchmark={'status':'passed','comparison_rows':comparison['comparison_rows'],'secondary_failure_rollback':'passed'}
        after=fingerprint()
        if before!=after:raise ValueError('Persistent state changed during private rehearsal')
        raw.rollback()
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','captured_at':datetime.now(timezone.utc).isoformat(),
        'role':role,'nav_observations':len(actual),'months':len(results),'results':results,'candidate_code_sha256':bundle['hashes'],
        'persistent_state_unchanged':True,'database_persistent_writes':0,'tenant_isolation':'passed',
        'isolation':'session-local PostgreSQL temporary tables, not a separate server','reports_released':False,
        'installed_candidate_verified':bool(bundle.get('installed_candidate')),
        'operating_candidate':bundle.get('operating_candidate'),'operating_benchmark':operating_benchmark,
        'onboarding_acceptance':None if onboarding is None else {'status':'passed','rollback_verified':True,
            'as_of':onboarding['as_of'],'comparison_rows':onboarding['benchmark']['comparison_rows']}}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'failed','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,AssertionError)) else
            str(error.diag.message_primary) if isinstance(error,psycopg.ProgrammingError) else 'Service details redacted',
            'constraint':error.diag.constraint_name if isinstance(error,psycopg.Error) else None}),flush=True)
        raise SystemExit(1)
