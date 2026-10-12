"""Missing-only original cash recovery. No Flex calls, updates, deletes or migrations."""
import hashlib,json,os,sys
from datetime import datetime,timezone
from decimal import Decimal as D
from xml.etree import ElementTree as ET

TENANT='00000000-0000-0000-0000-000000000001'
ACCOUNTS=('U16088571','U16088606')
DAYS=('2026-08-27','2026-08-28')
SOURCES=dict(zip(DAYS,('245a073d90d79f407e6040e8ddc763a0be7a4383c5e9e0b1d1ce3bff22d3e7e7',
                     'de234481d8b5224cc6c68d26e177937f89952b3effb9b9539fe066de654f870f')))
PREFIX='review/alpha-world-t/cash-recovery-20261009-r2/'
RUN_TYPE='archived-cash-only-recovery-20261009'
KEY_PREFIX='archived-cash:v1:'
FIELDS=('ending_cash_text','settled_cash_text','fx_rate_to_base_text','row_sha256')
PROTECTED=('accounts','instruments','nav_snapshots','position_snapshots','trades','cash_transactions','fx_rates',
 'corporate_actions','fx_transactions','raw_reports','derived_portfolio_nav','synthetic_portfolio_components',
 'derived_portfolio_cash_ledger','performance_comparison_daily','benchmark_prices','reference_fx_rates',
 'risk_free_rates','market_data_prices','platform_subjects','subject_calculation_definitions','reporting_snapshots',
 'report_generation_runs','internal_report_artifacts','account_onboarding_cases','account_onboarding_approvals',
 'risk_analytics_snapshots','onboarding_historical_report_batches','subject_operating_registrations','subject_report_templates',
 'onboarding_historical_report_items','report_artifact_releases','report_release_decisions','report_runs','subject_report_template_assets')

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sha(v):return hashlib.sha256(v).hexdigest()
def number(v):
    if v in (None,''):return None
    n=D(v)
    if not n.is_finite():raise ValueError('Nonfinite broker cash')
    return format(n,'f')

def parse_source(raw,day,digest):
    if day not in SOURCES or digest!=SOURCES[day] or sha(raw)!=digest:raise ValueError('Unapproved source hash/date')
    rows=[]
    for e in ET.fromstring(raw).iter('CashReportCurrency'):
        a=dict(e.attrib)
        if a.get('accountId') not in ACCOUNTS:continue
        if a.get('fromDate')!=day.replace('-','') or a.get('toDate')!=day.replace('-',''):raise ValueError('Not an exact single-day cash statement')
        row={'account_id':a['accountId'],'snapshot_date':day,'currency':a['currency'],
             'ending_cash_text':number(a.get('endingCash')),'settled_cash_text':number(a.get('endingSettledCash')),
             'fx_rate_to_base_text':number(a.get('fxRateToBase')),'row_sha256':sha(canonical(a).encode()),'attributes':a}
        if row['ending_cash_text'] is None:raise ValueError('Missing ending cash')
        row['source_key']=KEY_PREFIX+day+':'+a['currency']+':'+digest
        rows.append(row)
    wanted={('U16088571','BASE_SUMMARY'),*{('U16088606',c) for c in ('BASE_SUMMARY','HKD','TWD','USD')}}
    if len(rows)!=5 or {(r['account_id'],r['currency']) for r in rows}!=wanted:raise ValueError('Unexpected cash currency set or duplicate')
    return rows

def validate_financials(c,rows):
    for day in DAYS:
        for account in ACCOUNTS:
            group=[r for r in rows if r['snapshot_date']==day and r['account_id']==account]
            base=next(r for r in group if r['currency']=='BASE_SUMMARY')
            nav=c.execute("SELECT cash_text FROM nav_snapshots WHERE account_id=? AND snapshot_date=? AND currency='USD'",(account,day)).fetchall()
            if len(nav)!=1 or D(nav[0][0])!=D(base['ending_cash_text']):raise ValueError('Original cash differs from stored NAV')
            if account=='U16088606':
                total=D(0)
                for r in group:
                    currency=r['currency']
                    if currency=='BASE_SUMMARY':continue
                    if currency=='USD':rate=D(1)
                    else:
                        rates=c.execute("SELECT rate_text FROM fx_rates WHERE account_id=? AND rate_date=? AND from_currency=? AND to_currency='USD'",(account,day,currency)).fetchall()
                        if len(rates)!=1:raise ValueError('Ambiguous exact-date FX')
                        rate=D(rates[0][0])
                    total+=D(r['ending_cash_text'])*rate
                if abs(total-D(base['ending_cash_text']))>D('.000001'):raise ValueError('Currency cash total differs from base summary')

def existing(c,rows):
    found=0
    for r in rows:
        old=c.execute('SELECT ending_cash_text,settled_cash_text,fx_rate_to_base_text,row_sha256 FROM cash_snapshots WHERE account_id=? AND snapshot_date=? AND currency=?',tuple(r[k] for k in ('account_id','snapshot_date','currency'))).fetchone()
        if old:
            if any(old[i]!=r[k] for i,k in enumerate(FIELDS)):raise ValueError('Existing cash conflicts; no overwrite permitted')
            found+=1
    if found not in (0,10):raise ValueError('Partial prior cash recovery requires investigation')
    total=c.execute("SELECT count(*) FROM cash_snapshots WHERE account_id IN ('U16088571','U16088606') AND snapshot_date IN ('2026-08-27','2026-08-28')").fetchone()[0]
    if total!=found:raise ValueError('Unexpected existing currency rows in recovery scope')
    return found

def write_rows(c,rows,runs):
    if existing(c,rows)==10:return 0
    for r in rows:
        run=runs[r['snapshot_date']]
        c.execute('''INSERT INTO flex_rows(account_id,section,record_type,source_key,report_date,conid,currency,
            row_sha256,data_json,first_seen_run_id,last_seen_run_id,tenant_id) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?)''',
            (r['account_id'],'CashReport','CashReportCurrency',r['source_key'],r['snapshot_date'],r['currency'],
             r['row_sha256'],canonical(r['attributes']),run,run,TENANT))
        c.execute('''INSERT INTO cash_snapshots(account_id,snapshot_date,currency,ending_cash_text,settled_cash_text,
            fx_rate_to_base_text,row_sha256,last_seen_run_id) VALUES(?,?,?,?,?,?,?,?)''',
            tuple(r[k] for k in ('account_id','snapshot_date','currency',*FIELDS))+(run,))
    return 10

def fingerprint(c):
    queries={t:'SELECT to_jsonb(t)::text FROM '+t+' t ORDER BY to_jsonb(t)::text' for t in PROTECTED
             if c.execute('SELECT to_regclass(?)',('public.'+t,)).fetchone()[0]}
    queries['other_cash']="SELECT to_jsonb(t)::text FROM cash_snapshots t WHERE NOT (account_id IN ('U16088571','U16088606') AND snapshot_date IN ('2026-08-27','2026-08-28')) ORDER BY to_jsonb(t)::text"
    queries['other_flex']="SELECT to_jsonb(t)::text FROM flex_rows t WHERE source_key NOT LIKE 'archived-cash:v1:%' ORDER BY to_jsonb(t)::text"
    queries['other_runs']="SELECT to_jsonb(t)::text FROM ingestion_runs t WHERE run_type<>'"+RUN_TYPE+"' ORDER BY to_jsonb(t)::text"
    result={}
    for key,query in queries.items():
        h=hashlib.sha256();n=0
        for r in c.execute(query):h.update(r[0].encode());h.update(b'\n');n+=1
        result[key]={'rows':n,'sha256':h.hexdigest()}
    return result

def main():
    sys.path[:0]=['/app/src','/app/scripts']
    import psycopg
    from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient,ContentSettings
    mode=os.environ['T_CASH_MODE']
    if mode not in ('rehearse','apply','verify'):raise ValueError('Invalid mode')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def load(name):return service.get_blob_client('internal-reports',PREFIX+name+'.json').download_blob().readall()
    if mode in ('apply','verify'):
        prior=load('rehearse' if mode=='apply' else 'apply')
        if sha(prior)!=os.environ['T_CASH_PRIOR_SHA']:raise ValueError('Prerequisite receipt changed')
        prior=json.loads(prior)
        if not prior['passed'] or prior['source_code_sha256']!=os.environ['T_CASH_CODE_SHA']:raise ValueError('Unqualified code')
    rows=[];archives={}
    for day,digest in SOURCES.items():
        name=TENANT+'/recovery/20261007/flex_'+day+'_'+digest+'.xml'
        raw=service.get_blob_client('ibkr-raw',name).download_blob().readall()
        rows+=parse_source(raw,day,digest);archives[day]=name
    outcome={'mode':mode,'source_code_sha256':os.environ['T_CASH_CODE_SHA'],'sources':SOURCES,
       'started_at':datetime.now(timezone.utc).isoformat(),'new_flex_requests':0}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
        options='-c statement_timeout=180000 -c lock_timeout=15000') as raw:
        c=PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'+(' READ ONLY' if mode=='verify' else ''))
        c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
        if mode!='verify':
            if not c.execute('SELECT pg_try_advisory_xact_lock(hashtext(?))',('ibkr-daily:'+TENANT,)).fetchone()[0]:raise ValueError('Ingestion busy')
            c.execute('LOCK TABLE cash_snapshots,flex_rows IN SHARE ROW EXCLUSIVE MODE')
        validate_financials(c,rows);before=fingerprint(c);initial=existing(c,rows)
        outcome.update(initial_cash_rows=initial,protected_before=before)
        if mode=='verify':
            if initial!=10 or before!=prior['protected_after']:raise ValueError('Post-apply protected state differs')
            for r in rows:
                record=c.execute('''SELECT f.data_json,f.row_sha256,f.last_seen_run_id,c.last_seen_run_id
                   FROM flex_rows f JOIN cash_snapshots c ON c.account_id=f.account_id AND c.snapshot_date=f.report_date AND c.currency=f.currency
                   WHERE f.account_id=? AND f.source_key=?''',(r['account_id'],r['source_key'])).fetchall()
                if len(record)!=1 or record[0][0]!=canonical(r['attributes']) or record[0][1]!=r['row_sha256'] or record[0][2]!=prior['run_ids'][r['snapshot_date']] or record[0][3]!=record[0][2]:raise ValueError('Cash provenance linkage differs')
            if c.execute('SELECT count(*) FROM ingestion_runs WHERE run_type=?',(RUN_TYPE,)).fetchone()[0]!=2:raise ValueError('Recovery audit run count differs')
            outcome.update(inserted=0,protected_after=before,passed=True,read_only=True)
            raw.rollback()
        else:
            if mode=='apply' and (initial!=0 or before!=prior['protected_after']):raise ValueError('State drifted since rehearsal')
            if initial!=0:raise ValueError('Recovery already populated; verification required')
            now=datetime.now(timezone.utc).isoformat();runs={}
            for day,digest in SOURCES.items():
                if c.execute('SELECT count(*) FROM raw_reports WHERE content_sha256=?',(digest,)).fetchone()[0]!=1:raise ValueError('Original archive registration absent')
                runs[day]=c.execute('''INSERT INTO ingestion_runs(run_type,requested_from,requested_to,started_at,completed_at,status,
                   raw_sha256,raw_path,row_count,reconciliation_json,tenant_id) VALUES(?,?,?,?,?,'success',?,?,5,?,?) RETURNING run_id''',
                   (RUN_TYPE,day,day,now,now,digest,os.environ['AZURE_STORAGE_ACCOUNT_URL']+'/ibkr-raw/'+archives[day],
                    canonical({'authorization':'Owner requested continuation of scoped cash backfill','mode':mode,
                        'no_source_overwrite':True,'source_code_sha256':os.environ['T_CASH_CODE_SHA']}),TENANT)).fetchone()[0]
            inserted=write_rows(c,rows,runs)
            if inserted!=10 or existing(c,rows)!=10:raise ValueError('Insertion coverage incorrect')
            if write_rows(c,rows,runs)!=0:raise ValueError('Replay was not idempotent')
            validate_financials(c,rows);after=fingerprint(c)
            if before!=after:raise ValueError('Protected financial/report state changed')
            outcome.update(inserted=inserted,run_ids=runs,protected_after=after,idempotent_replay_inserts=0,passed=True)
            if mode=='rehearse':
                raw.rollback()
                c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
                if existing(c,rows)!=0 or fingerprint(c)!=before:raise ValueError('Rollback failed')
                if c.execute('SELECT count(*) FROM ingestion_runs WHERE run_type=?',(RUN_TYPE,)).fetchone()[0]!=0:raise ValueError('Trial audit rows survived rollback')
                raw.rollback();outcome['rollback_verified']=True
            else:raw.commit();outcome['committed']=True
    content=json.dumps(outcome,sort_keys=True,indent=2).encode()
    name=PREFIX+mode+'.json';blob=service.get_blob_client('internal-reports',name)
    blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if blob.download_blob().readall()!=content:raise ValueError('Receipt readback mismatch')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'cash_'+mode+'_passed','passed':True,'mode':mode,
       'inserted':outcome['inserted'],'blob':name,'sha256':sha(content),'rollback_verified':outcome.get('rollback_verified'),
       'protected_state_unchanged':True,'new_flex_requests':0}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(e).__name__,
           'error':str(e) if isinstance(e,(ValueError,KeyError)) else 'Service details redacted','mode':os.environ.get('T_CASH_MODE')}),flush=True)
        raise SystemExit(1)
