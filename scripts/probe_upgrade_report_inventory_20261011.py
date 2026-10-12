"""Read-only inventory and no-write replay of all five immutable QA packages."""
import contextlib,hashlib,inspect,io,json,os,socket,sys
from pathlib import Path
from unittest.mock import patch
import qa_boundary as q

def main():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    q.DATABASE='upgrade_g04_fresh_20261011'
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Recovery endpoint differs')
    source=Path('/app/scripts/generate_month_end_package.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Installed worker changed')
    pilot={k:os.environ[k] for k in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256')}
    if pilot['FACTSHEET_TEMPLATE_SHA256']!='460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d':raise ValueError('Pilot design changed')
    os.environ['DATABASE_URL']=q.isolated_uri(os.environ['DATABASE_URL'])
    os.environ.update(REPORT_PERIOD_END=q.PERIOD,INTERNAL_REPORT_CONTAINER=q.CONTAINER)
    for k in ('REPORT_DESIGN_VERSION','REPORT_BOUND_SNAPSHOT_ID','REPORT_BOUND_SNAPSHOT_SHA256','REPORTING_PERIOD_START'):os.environ.pop(k,None)
    original=psycopg.connect
    def guarded(conninfo='',**kwargs):
        opts=conninfo_to_dict(conninfo,**kwargs)
        if opts.get('host')!=q.HOST or opts.get('dbname')!=q.DATABASE or opts.get('hostaddr') or opts.get('service'):raise ValueError('Non-QA connection prohibited')
        kwargs.update(host=q.HOST,port=5432,dbname=q.DATABASE,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c app.tenant_id='+q.TENANT)
        return original(conninfo,**kwargs)
    psycopg.connect=guarded;sys.path[:0]=['/app/src','/app/scripts']
    import generate_month_end_package as worker
    import ms_review_upload as ms
    import ibkr_etl.cloud_storage as storage_module
    from ibkr_etl.db import connect
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    storage=os.environ['AZURE_STORAGE_ACCOUNT_URL']
    if storage!='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net':raise ValueError('Wrong storage')
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    service=BlobServiceClient(storage,credential=credential);bucket=service.get_container_client(q.CONTAINER)
    if bucket.get_container_properties().get('public_access') is not None:raise ValueError('QA container is public')
    inventory={r.name:(str(r.etag),r.size) for r in bucket.list_blobs()}
    if set(inventory)!=set(EXPECTED_BLOBS):raise ValueError('Unexpected QA blob inventory; reconcile without deleting')
    for name,digest in EXPECTED_BLOBS.items():
        content=bucket.get_blob_client(name).download_blob().readall()
        if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('QA inventory content differs')
    independent_binding=ms.upload_bytes is worker.upload_bytes
    helper_source=inspect.getsource(ms.upload_ms_review)
    if not independent_binding or 'overwrite=False' not in helper_source:raise ValueError('Unexpected MS upload helper')
    # A worker namespace patch does not intercept the helper module's imported binding.
    with patch.object(worker,'upload_bytes',object()):
        if ms.upload_bytes is worker.upload_bytes:raise ValueError('Expected independently bound MS upload path')
    def prohibited(*a,**k):raise ValueError('Replay attempted rendering, uploading or commentary')
    def fingerprint():
        db=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],q.TENANT)
        try:
            if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
            return {t:hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest()
                for t in ('reporting_snapshots','report_generation_runs','report_artifact_releases','report_release_decisions','audit_events')}
        finally:db.close()
    before=fingerprint();replayed=[]
    for subject in ('U16088571','U16088606','alpha-world-ms','alpha-world-t','hong-kong-china'):
        os.environ['PILOT_ACCOUNT_ID']=subject
        for k in pilot:os.environ.pop(k,None)
        if subject=='U16088571':os.environ.update(pilot)
        capture=io.StringIO()
        with patch.object(worker,'upload_bytes',prohibited),patch.object(worker,'upload_ms_review',prohibited),patch.object(ms,'upload_bytes',prohibited),patch.object(storage_module,'upload_bytes',prohibited),patch.object(worker,'render_factsheet',prohibited),patch.object(worker,'generate_month_end_commentary',prohibited),contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
            status=worker.main()
        outcomes=[]
        for line in capture.getvalue().splitlines():
            try:item=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(item,dict) and item.get('status')=='already_generated':outcomes.append(item)
        if status or len(outcomes)!=1:raise ValueError('Replay did not accept immutable pair')
        replayed.append(subject)
    if fingerprint()!=before or inventory!={r.name:(str(r.etag),r.size) for r in bucket.list_blobs()}:raise ValueError('Read-only replay changed state')
    service.close();credential.close()
    print('UPGRADE_REPORT_INVENTORY '+json.dumps({'passed':True,'qa_blobs_hash_verified':len(inventory),'fresh_artifacts':10,'prior_artifacts_preserved':len(inventory)-10,
        'replayed_subjects':replayed,'ms_independent_upload_binding_confirmed':True,'ms_helper_sha256':hashlib.sha256(helper_source.encode()).hexdigest(),
        'database_writes':0,'uploads_allowed':False,'production_connection_used':False,'all_replay_writes_prohibited':True,
        'original_failed_trial_preserved':True,'g04_complete':False}),flush=True)

if __name__=='__main__':main()
