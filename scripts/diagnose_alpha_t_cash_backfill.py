"""Read-only diagnosis of post-commit differences; never repeats the import."""
import base64,zlib,os,json,hashlib,sys
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings

def main():
    source=zlib.decompress(base64.b64decode(os.environ['T_CASH_HELPER']))
    helper={'__name__':'cash_helper'};exec(compile(source,'cash-helper','exec'),helper)
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    prefix=helper['PREFIX'];prior=json.loads(service.get_blob_client('internal-reports',prefix+'apply.json').download_blob().readall())
    if hashlib.sha256(source).hexdigest()!=prior['source_code_sha256']:raise ValueError('Helper binding differs')
    rows=[]
    for day,digest in helper['SOURCES'].items():
        content=service.get_blob_client('ibkr-raw',helper['TENANT']+'/recovery/20261007/flex_'+day+'_'+digest+'.xml').download_blob().readall()
        rows+=helper['parse_source'](content,day,digest)
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,options='-c statement_timeout=180000') as raw:
        c=PostgresConnection(raw);c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)",(helper['TENANT'],))
        count=helper['existing'](c,rows);helper['validate_financials'](c,rows)
        current=helper['fingerprint'](c)
        changes={k:{'before':prior['protected_after'].get(k),'after':v} for k,v in current.items() if prior['protected_after'].get(k)!=v}
        # JSON serialization can change no values, but hashes of typed SQL rows
        # may reflect session settings. Capture those settings without secrets.
        settings={k:c.execute('SHOW '+k).fetchone()[0] for k in ('timezone','DateStyle','extra_float_digits','transaction_read_only')}
        cash=[dict(r) for r in c.execute("SELECT * FROM cash_snapshots WHERE account_id IN ('U16088571','U16088606') AND snapshot_date IN ('2026-08-27','2026-08-28') ORDER BY account_id,snapshot_date,currency")]
        runs=[dict(r) for r in c.execute('SELECT run_id,run_type,status,started_at,completed_at FROM ingestion_runs WHERE started_at>=? ORDER BY run_id',(prior['started_at'],))]
        releases=[dict(r) for r in c.execute('''SELECT a.artifact_release_id,a.generation_run_id,a.release_decision_id,a.released_at,
            a.revoked_at,g.status,g.report_type,g.completed_at,s.subject_id,s.period_end
            FROM report_artifact_releases a JOIN report_generation_runs g USING(generation_run_id)
            JOIN reporting_snapshots s USING(snapshot_id)
            WHERE a.released_at::timestamptz>=?::timestamptz ORDER BY a.artifact_release_id''',(prior['started_at'],))]
        reconstructed={}
        ids=[r['artifact_release_id'] for r in releases]
        generation_ids=[r['generation_run_id'] for r in releases]
        def query_hash(sql,args):
            h=hashlib.sha256();n=0
            for r in c.execute(sql,args):h.update(r[0].encode());h.update(b'\n');n+=1
            return {'rows':n,'sha256':h.hexdigest()}
        if ids:
            reconstructed['release_table_without_later_rows']=query_hash('SELECT to_jsonb(t)::text FROM report_artifact_releases t WHERE NOT (artifact_release_id=ANY(?)) ORDER BY to_jsonb(t)::text',(ids,))
            for status in ('awaiting_approval','completed','generated','success','succeeded','private_generated','pending_release'):
                query="SELECT (CASE WHEN generation_run_id=ANY(?) THEN to_jsonb(t)||jsonb_build_object('status',?::text) ELSE to_jsonb(t) END)::text AS row_json FROM report_generation_runs t ORDER BY row_json"
                value=query_hash(query,(generation_ids,status))
                if value==prior['protected_after']['report_generation_runs']:reconstructed['prior_generation_status']=status
        source_links=[]
        for r in rows:
            matches=c.execute('''SELECT f.data_json,f.row_sha256,f.last_seen_run_id,c.last_seen_run_id
                 FROM flex_rows f JOIN cash_snapshots c ON c.account_id=f.account_id AND c.snapshot_date=f.report_date AND c.currency=f.currency
                 WHERE f.account_id=? AND f.source_key=?''',(r['account_id'],r['source_key'])).fetchall()
            ok=len(matches)==1 and matches[0][0]==helper['canonical'](r['attributes']) and matches[0][1]==r['row_sha256'] and matches[0][2]==matches[0][3]==prior['run_ids'][r['snapshot_date']]
            source_links.append(ok)
        raw.rollback()
    result={'status':'diagnosed','read_only':True,'cash_rows':count,'source_links_verified':all(source_links),
        'protected_differences':changes,'current_fingerprints':current,'settings':settings,'cash':cash,'recent_ingestion_runs':runs,
        'later_releases':releases,'reconstructed_baseline':reconstructed,
        'captured_at':datetime.now(timezone.utc).isoformat()}
    data=json.dumps(result,sort_keys=True,indent=2,default=str).encode();digest=hashlib.sha256(data).hexdigest()
    name=prefix+'diagnose-'+digest+'.json';blob=service.get_blob_client('internal-reports',name)
    blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if blob.download_blob().readall()!=data:raise ValueError('Readback failed')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'diagnosed','blob':name,'sha256':digest,
       'cash_rows':count,'source_links_verified':all(source_links),'protected_differences':changes,'recent_ingestion_runs':runs,
       'later_releases':releases,'reconstructed_baseline':reconstructed},default=str),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(e).__name__,'error':str(e) if isinstance(e,ValueError) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
