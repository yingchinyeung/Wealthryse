"""Exact MS-only transaction routines, shared by isolated qualification and release."""
import json
import math
from dataclasses import asdict
from datetime import date, timedelta, datetime, timezone

from ibkr_etl.benchmark_analytics import BenchmarkContract, build_comparison
from ibkr_etl.provisional_benchmark import convert_levels_to_reporting_currency
from ibkr_etl.website_benchmarks import MS_HKD_COMPARISON_VERSION, version_website_comparison

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'
MAP_COLUMNS='benchmark_id,mapping_type,effective_from,effective_to,reporting_currency,created_at'
ROW_COLUMNS=('observation_date','portfolio_value','benchmark_level','normalized_portfolio',
             'normalized_benchmark','portfolio_return','benchmark_return','excess_return')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)


def require(ok,message):
    if not ok:raise ValueError(message)


def table(namespace,name):
    require(namespace in ('public','pg_temp'),'Only explicit live or own-temporary namespace is allowed')
    require(name in ('portfolio_benchmark_mapping','performance_comparison_daily','audit_events'),
            'Table outside MS cutover allowlist')
    return namespace+'.'+name


def comparisons(inputs,proof):
    require(not inputs['external_flows'],'Non-zero flows require new qualification')
    require(len(inputs['subject'])==1 and inputs['subject'][0]['subject_id']==SUBJECT
        and inputs['subject'][0]['base_currency']=='HKD' and inputs['subject'][0]['reporting_currency']=='HKD'
        and inputs['subject'][0]['lifecycle_status']=='onboarding','MS contract/lifecycle changed')
    nav=[(r['snapshot_date'],float(r['total_nav'])) for r in inputs['nav']]
    require(all(r['currency']=='HKD' for r in inputs['nav']),'NAV currency differs')
    end=max(day for day,value in nav)
    masters={r['benchmark_id']:r for r in inputs['benchmark_master']}
    require(set(masters)=={'GSPC','HSI'},'Benchmark pair differs')
    fx=[(r['rate_date'],float(r['rate'])) for r in inputs['fx']
        if r['from_currency']=='USD' and r['to_currency']=='HKD'
        and r['provider']=='Yahoo Finance via yfinance']
    expected={r['benchmark_id']:r for r in proof['endpoints']}
    result=[]
    for identifier in ('GSPC','HSI'):
        master=masters[identifier]
        require(master['active'] and master['return_convention']=='price'
            and master['symbol']==('^GSPC' if identifier=='GSPC' else '^HSI')
            and master['currency']==('USD' if identifier=='GSPC' else 'HKD'),'Source benchmark contract differs')
        levels=[(r['observation_date'],float(r['level'])) for r in inputs['prices']
            if r['benchmark_id']==identifier and r['observation_status']=='provisional_close']
        if identifier=='GSPC':levels,_=convert_levels_to_reporting_currency(levels,fx)
        built=version_website_comparison(build_comparison(subject_id=SUBJECT,reporting_currency='HKD',
            portfolio_currency='HKD',portfolio_values=nav,cash_flows={},
            benchmark=BenchmarkContract(identifier,'HKD','price','provisional_close','restricted'),
            benchmark_levels=levels,start_date='2025-01-20',expected_as_of=end,
            minimum_return_observations=30,development_mode=True,required_return_convention='price'))
        require(built.quality_status in ('pass','warn'),'Candidate comparison failed data quality')
        require(len(built.rows)==expected[identifier]['observations'],'Qualified coverage differs')
        actual=built.metrics;wanted=expected[identifier]['metrics']
        require(actual.keys()==wanted.keys(),'Qualified metric field set differs')
        for key,value in wanted.items():
            observed=actual[key]
            if isinstance(value,(int,float)):
                require(observed is not None and math.isclose(observed,value,rel_tol=1e-9,abs_tol=1e-10),
                        'Qualified native metric differs: '+identifier+':'+key)
            else:require(observed==value,'Qualified native metric convention differs: '+key)
        result.append(built)
    return result


def mappings(c,namespace):
    return [dict(r) for r in c.execute('SELECT '+MAP_COLUMNS+' FROM '+table(namespace,'portfolio_benchmark_mapping')+
        ' WHERE tenant_id=%s AND subject_id=%s ORDER BY benchmark_id,effective_from',(TENANT,SUBJECT))]


def current_candidate_mapping(inputs,day,stamp):
    prior=date.fromisoformat(day)-timedelta(days=1)
    result=[]
    for row in inputs['mappings']:
        require(row['effective_to'] is None and row['effective_from']<day,'Original mapping interval differs')
        result.append({**row,'effective_to':prior.isoformat()})
    for identifier,kind in (('GSPC','primary'),('HSI','secondary')):
        result.append(dict(benchmark_id=identifier,mapping_type=kind,effective_from=day,
            effective_to=None,reporting_currency='HKD',created_at=stamp))
    return sorted(result,key=lambda r:(r['benchmark_id'],r['effective_from']))


def transaction_guard(c,day):
    require(c.execute('SHOW transaction_isolation').fetchone()['transaction_isolation']=='serializable',
            'Serializable transaction required')
    require(c.execute("SELECT current_setting('app.tenant_id') AS tenant").fetchone()['tenant']==TENANT,
            'Tenant context differs')
    require(c.execute('SELECT CURRENT_DATE::text AS day').fetchone()['day']==day,'Approved release date expired')
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(TENANT+':'+SUBJECT+':website-cutover',))


def audit(c,namespace,stamp,event,details):
    c.execute('INSERT INTO '+table(namespace,'audit_events')+
        '(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) '
        "VALUES(%s,%s,'agent','codex-owner-authorized-ms-website-cutover',%s,%s,'succeeded',%s)",
        (TENANT,datetime.now(timezone.utc).isoformat(),event,SUBJECT,canonical(details)))


def apply(c,*,namespace,inputs,results,day,stamp,binding,failure_after_rows=None):
    transaction_guard(c,day)
    require(canonical(mappings(c,namespace))==canonical(inputs['mappings']),'Original mapping drift; refuse overwrite')
    require([r.benchmark_id for r in results]==['GSPC','HSI'] and all(r.subject_id==SUBJECT
        and r.reporting_currency=='HKD' and r.calculation_version==MS_HKD_COMPARISON_VERSION
        and r.quality_status in ('pass','warn') for r in results),'Qualified result pair differs')
    count=c.execute('SELECT count(*) AS n FROM '+table(namespace,'performance_comparison_daily')+
        ' WHERE tenant_id=%s AND subject_id=%s AND calculation_version=%s',
        (TENANT,SUBJECT,MS_HKD_COMPARISON_VERSION)).fetchone()['n']
    require(count==0,'Version already exists; inspect original release rather than repeat')
    c.execute('SAVEPOINT ms_exact_cutover')
    try:
        prior=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
        changed=c.execute('UPDATE '+table(namespace,'portfolio_benchmark_mapping')+
            ' SET effective_to=%s WHERE tenant_id=%s AND subject_id=%s AND effective_to IS NULL',
            (prior,TENANT,SUBJECT)).rowcount
        require(changed==4,'Original mapping update count differs')
        for identifier,kind in (('GSPC','primary'),('HSI','secondary')):
            c.execute('INSERT INTO '+table(namespace,'portfolio_benchmark_mapping')+
                '(tenant_id,subject_id,benchmark_id,mapping_type,effective_from,reporting_currency,created_at) '
                "VALUES(%s,%s,%s,%s,%s,'HKD',%s)",(TENANT,SUBJECT,identifier,kind,day,stamp))
        written=0
        for result in results:
            for row in result.rows:
                values=asdict(row)
                c.execute('INSERT INTO '+table(namespace,'performance_comparison_daily')+
                    '(tenant_id,subject_id,benchmark_id,'+','.join(ROW_COLUMNS)+
                    ',reporting_currency,calculation_version,quality_status,generated_at) '
                    'VALUES('+','.join(['%s']*15)+')',
                    (TENANT,SUBJECT,result.benchmark_id,*[values[k] for k in ROW_COLUMNS],
                     'HKD',MS_HKD_COMPARISON_VERSION,result.quality_status,stamp))
                written+=1
                if failure_after_rows==written:raise RuntimeError('Injected exact writer failure')
        require(canonical(mappings(c,namespace))==canonical(current_candidate_mapping(inputs,day,stamp)),
                'Installed mapping pair differs')
        audit(c,namespace,stamp,'ms_website_hkd_cutover',dict(binding=binding,rows=written,
            original_mappings=inputs['mappings'],candidate_mappings=current_candidate_mapping(inputs,day,stamp)))
        c.execute('RELEASE SAVEPOINT ms_exact_cutover')
        return written
    except Exception:
        c.execute('ROLLBACK TO SAVEPOINT ms_exact_cutover');c.execute('RELEASE SAVEPOINT ms_exact_cutover')
        raise


def rollback(c,*,namespace,inputs,day,stamp,binding):
    transaction_guard(c,day)
    candidate=current_candidate_mapping(inputs,day,stamp)
    require(canonical(mappings(c,namespace))==canonical(candidate),'Rollback selection drift; refuse overwrite')
    c.execute('SAVEPOINT ms_exact_rollback')
    try:
        # Delete only the two rows created by this release, whose full state was
        # just checked. They are retained in the protected plan and audit details.
        n=c.execute('DELETE FROM '+table(namespace,'portfolio_benchmark_mapping')+
            " WHERE tenant_id=%s AND subject_id=%s AND benchmark_id IN ('GSPC','HSI') "
            "AND effective_from=%s AND created_at=%s AND reporting_currency='HKD'",
            (TENANT,SUBJECT,day,stamp)).rowcount
        require(n==2,'Owned rollback mapping count differs')
        for row in inputs['mappings']:
            n=c.execute('UPDATE '+table(namespace,'portfolio_benchmark_mapping')+
                ' SET effective_to=%s WHERE tenant_id=%s AND subject_id=%s AND benchmark_id=%s '
                'AND effective_from=%s AND created_at=%s',
                (row['effective_to'],TENANT,SUBJECT,row['benchmark_id'],row['effective_from'],row['created_at'])).rowcount
            require(n==1,'Original rollback mapping key differs')
        require(canonical(mappings(c,namespace))==canonical(inputs['mappings']),'Original selection not restored')
        audit(c,namespace,stamp,'ms_website_hkd_cutover_rollback',dict(binding=binding,
            restored_mappings=inputs['mappings'],removed_owned_mappings=candidate,
            versioned_comparison_history_preserved=True))
        c.execute('RELEASE SAVEPOINT ms_exact_rollback')
    except Exception:
        c.execute('ROLLBACK TO SAVEPOINT ms_exact_rollback');c.execute('RELEASE SAVEPOINT ms_exact_rollback')
        raise
