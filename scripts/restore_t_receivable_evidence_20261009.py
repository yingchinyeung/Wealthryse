"""Missing-only restoration of three archived broker evidence rows, never valuations."""
import base64, hashlib, json, os, sys, zlib
from datetime import datetime, timezone

TENANT='00000000-0000-0000-0000-000000000001'
RUN_TYPE='alpha-t-original-receivable-evidence-20261009'
PREFIX='review/alpha-world-t/receivable-evidence-20261009/'
SOURCES={
 ('2025-08-29','721987245'):('e1120b9a607d1a15c497c3915ee505e2c90495f5ae21d6e937b8d98171dc5881','f46300c9dd7eae73b24a369a0089f319fda4c720726e87ce81abd56ef58a7e29'),
 ('2026-07-31','720719835'):('3e3fe84b0b8ff04729e4c8e9aa6a214cfd972b59f85f3c30533d76f32dff1873','283fb22411efe2f77174d3b79d14f1dfd1ef705141a7b8835875c215173d823e'),
 ('2026-08-31','721987245'):('d725e09c1cb2a40bbe64da48ae64fb1ed608a5a031baf4b369d65083cfa9e543','7b7900576bf7962750ffd167e05e3355040020669237a627ea1bb93bba5407c8'),
}
PROTECTED=('accounts','platform_subjects','nav_snapshots','position_snapshots','cash_snapshots',
 'cash_transactions','trades','corporate_actions','fx_rates','derived_portfolio_nav',
 'synthetic_portfolio_components','subject_calculation_definitions','reporting_snapshots',
 'report_generation_runs','report_file_approvals','account_onboarding_cases','crisis_replay_components')

def require(condition,message):
    if not condition:raise ValueError(message)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def parse_sources(originals):
    from ibkr_etl.parser import parse_flex_xml
    from ibkr_etl.util import canonical_json
    result=[]
    for (day,conid),(digest,row_hash) in SOURCES.items():
        raw=originals[digest]
        require(sha(raw)==digest,'Original archive checksum differs')
        rows=[r for r in parse_flex_xml(raw).rows if r.account_id=='U16088606'
              and r.report_date==day and r.conid==conid and r.record_type=='OpenPosition'
              and r.row_sha256==row_hash]
        require(bool(rows) and all(r==rows[0] for r in rows),'Original broker row missing or ambiguous')
        row=rows[0]
        require(sha(canonical_json(row.attributes).encode())==row_hash,'Original broker attributes checksum differs')
        require(row.attributes.get('symbol','').endswith('.REC')
                and row.attributes.get('listingExchange')=='CORPACT','Original is not a broker receivable')
        result.append(row)
    return result

def validate_positions(c,rows):
    from ibkr_etl.util import decimal_text,pick
    for r in rows:
        a=r.attributes
        p=dict(account_id=r.account_id,snapshot_date=r.report_date,conid=r.conid,
            model_code=pick(a,'model','modelCode'),currency=r.currency,
            quantity_text=decimal_text(pick(a,'position','quantity')),
            cost_basis_text=decimal_text(pick(a,'costBasisMoney','costBasis')),
            mark_price_text=decimal_text(pick(a,'markPrice','closePrice')),
            market_value_text=decimal_text(pick(a,'positionValue','marketValue')),
            unrealized_pnl_text=decimal_text(pick(a,'fifoPnlUnrealized','unrealizedPL')),row_sha256=r.row_sha256)
        found=c.execute('''SELECT p.* FROM position_snapshots p JOIN accounts a ON a.account_id=p.account_id
            WHERE a.tenant_id=? AND p.account_id=? AND p.snapshot_date=? AND p.conid=? AND p.model_code=?''',
            (TENANT,r.account_id,r.report_date,r.conid,p['model_code'])).fetchall()
        require(len(found)==1,'Exact normalized position missing or ambiguous')
        old=dict(found[0])
        require(all(old[k]==v for k,v in p.items()),'Broker evidence differs from protected normalized position')

def write_rows(c,rows,run_ids):
    from ibkr_etl.util import canonical_json
    validate_positions(c,rows)
    count=0
    for r in rows:
        previous=c.execute('''SELECT tenant_id,row_sha256,data_json FROM flex_rows
            WHERE account_id=? AND section=? AND record_type=? AND source_key=?''',
            (r.account_id,r.section,r.record_type,r.source_key)).fetchall()
        if previous:
            require(len(previous)==1 and str(previous[0]['tenant_id'])==TENANT
                    and previous[0]['row_sha256']==r.row_sha256
                    and json.loads(previous[0]['data_json'])==r.attributes,
                    'Existing original evidence conflicts; no overwrite permitted')
            continue
        run=run_ids[r.report_date]
        c.execute('''INSERT INTO flex_rows(account_id,section,record_type,source_key,report_date,conid,currency,
            row_sha256,data_json,first_seen_run_id,last_seen_run_id,tenant_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (r.account_id,r.section,r.record_type,r.source_key,r.report_date,r.conid,r.currency,
             r.row_sha256,canonical_json(r.attributes),run,run,TENANT))
        count+=1
    return count

def fingerprint(c,rows):
    result={}
    for table in PROTECTED:
        if c.execute('SELECT to_regclass(?)',('public.'+table,)).fetchone()[0]:
            h=hashlib.sha256();n=0
            for r in c.execute('SELECT to_jsonb(t)::text FROM '+table+' t ORDER BY to_jsonb(t)::text'):
                h.update(r[0].encode());h.update(b'\n');n+=1
            result[table]={'rows':n,'sha256':h.hexdigest()}
    for name,sql,args in (
        ('other_flex','SELECT to_jsonb(t)::text FROM flex_rows t WHERE NOT (account_id=? AND source_key IN (?,?,?)) ORDER BY to_jsonb(t)::text',
         ('U16088606',*(r.source_key for r in rows))),
        ('other_runs','SELECT to_jsonb(t)::text FROM ingestion_runs t WHERE run_type<>? ORDER BY to_jsonb(t)::text',(RUN_TYPE,))):
        h=hashlib.sha256();n=0
        for r in c.execute(sql,args):h.update(r[0].encode());h.update(b'\n');n+=1
        result[name]={'rows':n,'sha256':h.hexdigest()}
    return result

def main():
    sys.path[:0]=['/app/src','/app/scripts']
    import psycopg
    from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
    from ibkr_etl.util import canonical_json
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient,ContentSettings
    from azure.core.exceptions import ResourceExistsError
    mode=os.environ['T_EVIDENCE_MODE'];require(mode in ('rehearse','apply','verify'),'Unknown mode')
    packed=''.join(os.environ['T_EVIDENCE_PART_'+str(i)] for i in range(int(os.environ['T_EVIDENCE_PARTS'])))
    bundle=json.loads(zlib.decompress(base64.b64decode(packed)))
    originals={k:zlib.decompress(base64.b64decode(v)) for k,v in bundle.items()}
    rows=parse_sources(originals)
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def blob(name):return service.get_blob_client('internal-reports',PREFIX+name)
    if mode in ('apply','verify'):
        prior_raw=blob(('rehearse' if mode=='apply' else 'apply')+'.json').download_blob().readall()
        require(sha(prior_raw)==os.environ['T_EVIDENCE_PRIOR_SHA'],'Prerequisite receipt changed')
        prior=json.loads(prior_raw)
        require(prior['passed'] and prior['source_code_sha256']==os.environ['T_EVIDENCE_CODE_SHA'],'Unqualified restoration code')
        if mode=='apply':require(prior['rollback_verified'],'Rollback rehearsal missing')
    for digest,raw in originals.items():
        b=blob('originals/'+digest+'.xml')
        try:b.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/xml'))
        except ResourceExistsError:pass
        require(sha(b.download_blob().readall())==digest,'Archived original readback differs')
    result={'mode':mode,'source_code_sha256':os.environ['T_EVIDENCE_CODE_SHA'],
        'started_at':datetime.now(timezone.utc).isoformat(),'sources':[{'date':k[0],'conid':k[1],'archive_sha256':v[0],'row_sha256':v[1]} for k,v in SOURCES.items()]}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c statement_timeout=180000 -c lock_timeout=15000') as raw:
        c=PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'+(' READ ONLY' if mode=='verify' else ''))
        c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
        if mode!='verify':
            require(c.execute('SELECT pg_try_advisory_xact_lock(hashtext(?))',('ibkr-daily:'+TENANT,)).fetchone()[0],'Ingestion busy')
            c.execute('LOCK TABLE flex_rows IN SHARE ROW EXCLUSIVE MODE')
        validate_positions(c,rows);before=fingerprint(c,rows)
        if mode in ('apply','verify'):require(before==prior['protected_after'],'Protected state drifted; requalification required')
        if mode=='verify':
            require(write_rows(c,rows,prior['run_ids'])==0,'Verification cannot insert evidence')
            require(c.execute('SELECT count(*) FROM ingestion_runs WHERE tenant_id=? AND run_type=?',(TENANT,RUN_TYPE)).fetchone()[0]==3,'Audit run coverage differs')
            result.update(inserted=0,passed=True,protected_after=before,read_only=True);raw.rollback()
        else:
            require(c.execute('SELECT count(*) FROM ingestion_runs WHERE tenant_id=? AND run_type=?',(TENANT,RUN_TYPE)).fetchone()[0]==0,'Prior restoration exists; reconcile it')
            now=datetime.now(timezone.utc).isoformat();runs={}
            for r in rows:
                digest=SOURCES[r.report_date,r.conid][0]
                runs[r.report_date]=c.execute('''INSERT INTO ingestion_runs(run_type,requested_from,requested_to,started_at,completed_at,status,
                    raw_sha256,raw_path,row_count,reconciliation_json,tenant_id) VALUES(?,?,?,?,?,'success',?,?,1,?,?) RETURNING run_id''',
                    (RUN_TYPE,r.report_date,r.report_date,now,now,digest,blob('originals/'+digest+'.xml').url,
                     canonical_json({'authorization':'Owner requested completion of T onboarding','mode':mode,'normalized_financial_data_unchanged':True}),TENANT)).fetchone()[0]
            inserted=write_rows(c,rows,runs);require(inserted==3,'Restoration coverage differs')
            require(write_rows(c,rows,runs)==0,'Restoration not idempotent')
            after=fingerprint(c,rows);require(before==after,'Protected state changed')
            result.update(inserted=3,passed=True,protected_after=after,run_ids=runs,idempotent_replay_inserts=0)
            if mode=='rehearse':
                raw.rollback();c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
                require(fingerprint(c,rows)==before,'Rollback protected state differs')
                require(c.execute('SELECT count(*) FROM ingestion_runs WHERE tenant_id=? AND run_type=?',(TENANT,RUN_TYPE)).fetchone()[0]==0,'Trial audit rows survived rollback')
                require(c.execute('SELECT count(*) FROM flex_rows WHERE tenant_id=? AND account_id=? AND source_key IN (?,?,?)',
                    (TENANT,'U16088606',*(r.source_key for r in rows))).fetchone()[0]==0,'Trial evidence survived rollback')
                raw.rollback();result['rollback_verified']=True
            else:raw.commit();result['committed']=True
    content=json.dumps(result,sort_keys=True,indent=2).encode();b=blob(mode+'.json')
    b.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    require(b.download_blob().readall()==content,'Receipt readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'evidence_'+mode+'_passed','passed':True,'mode':mode,
        'inserted':result['inserted'],'blob':PREFIX+mode+'.json','sha256':sha(content),
        'rollback_verified':result.get('rollback_verified'),'protected_state_unchanged':True}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(e).__name__,
            'error':str(e) if isinstance(e,(ValueError,KeyError)) else 'Service details redacted',
            'missing_module':e.name if isinstance(e,ModuleNotFoundError) else None}),flush=True)
        raise SystemExit(1)
