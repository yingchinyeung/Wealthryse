"""Exercise the installed worker for all subjects in the prepared QA copy only."""
import contextlib,hashlib,io,json,os,re,socket,sys,zipfile
from pathlib import Path
from unittest.mock import patch
import qa_boundary as q

STAGE='initialization'
SUBJECTS={'alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'}

def upload_allowed(url,container,name,kwargs,subject):
    return (url=='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net'
        and container=='upgrade-g04-reports-20261010'
        and subject in SUBJECTS and name.startswith('review/'+subject+'/2026-09-30/')
        and all(p not in ('','.','..') for p in name.split('/')) and kwargs.get('overwrite') is False)

def main():
    global STAGE
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    if os.getuid()!=10001:raise ValueError('Non-root required')
    q.DATABASE='upgrade_g04_fresh_20261011'
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong recovery IP')
    source=Path('/app/scripts/generate_month_end_package.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Worker source differs')
    source_template={k:os.environ[k] for k in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256')}
    if source_template['FACTSHEET_TEMPLATE_BLOB']!='templates/U16088571/monthly-factsheet/approved-august-2026-v2.pdf' or source_template['FACTSHEET_TEMPLATE_SHA256']!='460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d':raise ValueError('Pilot template binding changed')
    os.environ['DATABASE_URL']=q.isolated_uri(os.environ['DATABASE_URL'])
    os.environ.update(REPORT_PERIOD_END=q.PERIOD,INTERNAL_REPORT_CONTAINER=q.CONTAINER)
    for key in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256','REPORT_DESIGN_VERSION','REPORT_BOUND_SNAPSHOT_ID','REPORT_BOUND_SNAPSHOT_SHA256','REPORTING_PERIOD_START'):
        os.environ.pop(key,None)
    original_connect=psycopg.connect
    def guarded(conninfo='',**kwargs):
        opts=conninfo_to_dict(conninfo,**kwargs)
        if opts.get('host')!=q.HOST or opts.get('dbname')!=q.DATABASE or opts.get('hostaddr') or opts.get('service'):raise ValueError('Non-QA connection prohibited')
        kwargs.update(host=q.HOST,port=5432,dbname=q.DATABASE,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c statement_timeout=120000 -c app.tenant_id='+q.TENANT)
        return original_connect(conninfo,**kwargs)
    psycopg.connect=guarded
    sys.path[:0]=['/app/src','/app/scripts']
    import generate_month_end_package as worker
    from ibkr_etl.db import connect
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    def db():return connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],q.TENANT)
    def fingerprint(connection,table):
        if table not in ('reporting_snapshots','report_artifact_releases','report_release_decisions'):raise ValueError('Bad fingerprint scope')
        rows=connection.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+table+' t ORDER BY 1').fetchall()
        return hashlib.sha256('\n'.join(r['h'] for r in rows).encode()).hexdigest()
    connection=db()
    try:
        identity=dict(connection.execute('SELECT current_user AS role,current_database() AS database,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone())
        if identity!={'role':'ibkr_stg_app','database':q.DATABASE,'ssl':True}:raise ValueError('Wrong QA role or database')
        if connection.execute('SELECT count(*) AS n FROM report_generation_runs').fetchone()['n']!=0:raise ValueError('Empty QA generation registry required; reconcile any partial trial')
        rows=connection.execute('SELECT subject_id,snapshot_id,data_sha256 FROM reporting_snapshots WHERE tenant_id=?',(q.TENANT,)).fetchall()
        observed={r['subject_id']:{'snapshot_id':str(r['snapshot_id']),'sha256':r['data_sha256']} for r in rows}
        if observed!=EXPECTED_SNAPSHOTS or len(rows)!=5:raise ValueError('Fresh snapshot baseline changed')
        preserved={t:fingerprint(connection,t) for t in ('reporting_snapshots','report_artifact_releases','report_release_decisions')}
    finally:connection.close()
    storage=os.environ['AZURE_STORAGE_ACCOUNT_URL']
    if storage!='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net':raise ValueError('Unexpected storage')
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    service=BlobServiceClient(storage,credential=credential);bucket=service.get_container_client(q.CONTAINER)
    if bucket.get_container_properties().get('public_access') is not None:raise ValueError('QA container must remain private')
    before={r.name:(str(r.etag),r.size) for r in bucket.list_blobs()}
    upload=worker.upload_bytes;download=worker.download_blob_bytes
    def scoped_download(url,container,name,**kwargs):
        if container==q.CONTAINER and name==source_template['FACTSHEET_TEMPLATE_BLOB']:
            if url!=storage or q.SUBJECT!='U16088571':raise ValueError('Invalid pilot template read')
            content=download(url,'internal-reports',name,**kwargs)
            if hashlib.sha256(content).hexdigest()!=source_template['FACTSHEET_TEMPLATE_SHA256']:raise ValueError('Approved pilot template hash differs')
            return content
        return download(url,container,name,**kwargs)
    worker.download_blob_bytes=scoped_download
    results=[];uploads=[]
    def scoped_upload(url,container,name,content,**kwargs):
        if not upload_allowed(url,container,name,kwargs,q.SUBJECT):raise ValueError('Upload escaped QA boundary')
        result=upload(url,container,name,content,**kwargs)
        uploads.append(name);return result
    def run():
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):status=worker.main()
        if status:raise ValueError('Installed worker failed')
        outcomes=[]
        for line in capture.getvalue().splitlines():
            try:item=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(item,dict) and item.get('status') in ('generated','already_generated'):outcomes.append(item)
        if len(outcomes)!=1:raise ValueError('Unique installed-worker outcome missing')
        return outcomes[0]
    def prohibited(*a,**k):raise ValueError('Replay attempted new rendering or upload')
    for subject in sorted(SUBJECTS):
        q.SUBJECT=subject;os.environ['PILOT_ACCOUNT_ID']=subject;STAGE='generate:'+subject
        if subject=='U16088571':os.environ.update(source_template)
        else:
            for key in source_template:os.environ.pop(key,None)
        with patch.object(worker,'upload_bytes',scoped_upload):outcome=run()
        if outcome.get('status')!='generated' or outcome.get('snapshot_action')!='reused_existing':raise ValueError('Expected fresh artifact generation on frozen QA input')
        STAGE='replay:'+subject
        with patch.object(worker,'upload_bytes',prohibited),patch.object(worker,'render_factsheet',prohibited),patch.object(worker,'generate_month_end_commentary',prohibited):
            if run().get('status')!='already_generated':raise ValueError('Replay not idempotent')
        STAGE='readback:'+subject;connection=db();artifacts=[]
        try:
            records=q.metadata(connection)
            for kind,run_id in outcome['generation_run_ids'].items():
                row=next(r for r in records if r['generation_run_id']==run_id)
                if row['status']!='awaiting_approval' or not row['output_reference'].startswith(q.CONTAINER+'/review/'+subject+'/'):raise ValueError('QA output registration differs')
                content=download(storage,q.CONTAINER,row['output_reference'].split('/',1)[1],maximum_bytes=64*1024*1024)
                if hashlib.sha256(content).hexdigest()!=row['output_sha256']:raise ValueError('Output hash mismatch')
                if kind=='factsheet':
                    from pypdf import PdfReader
                    count=len(PdfReader(io.BytesIO(content)).pages)
                    if count!=2:raise ValueError('Expected two PDF pages')
                elif kind=='presentation':
                    with zipfile.ZipFile(io.BytesIO(content)) as z:count=sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml',n)) for n in z.namelist())
                    if count!=20:raise ValueError('Expected twenty slides')
                else:raise ValueError('Unknown report type')
                artifacts.append({'report_type':kind,'generation_run_id':run_id,'output_reference':row['output_reference'],'sha256':row['output_sha256'],'pages_or_slides':count})
            if len(artifacts)!=2 or any(fingerprint(connection,t)!=v for t,v in preserved.items()):raise ValueError('Snapshots or approvals changed')
        finally:connection.close()
        result={'subject':subject,'snapshot_id':EXPECTED_SNAPSHOTS[subject]['snapshot_id'],'generated':True,'idempotent_replay':True,'artifacts':artifacts}
        results.append(result);print('UPGRADE_ALL_REPORTS_SUBJECT '+json.dumps(result),flush=True)
    after={r.name:(str(r.etag),r.size) for r in bucket.list_blobs()}
    if any(after.get(n)!=v for n,v in before.items()) or set(after)-set(before)!=set(uploads)-set(before):raise ValueError('Existing QA outputs changed or unexpected write')
    connection=db()
    try:
        if connection.execute('SELECT count(*) AS n FROM report_generation_runs').fetchone()['n']!=10:raise ValueError('Expected ten QA generation records')
        if any(fingerprint(connection,t)!=v for t,v in preserved.items()):raise ValueError('Preserved state changed')
    finally:connection.close();service.close();credential.close()
    print('UPGRADE_ALL_REPORTS_FINAL '+json.dumps({'passed':True,'database':q.DATABASE,'subjects':results,'fresh_artifacts':10,'pilot_template_read_from_original_approved_location':True,
        'idempotent_replays':5,'existing_qa_blobs_preserved':len(before),'snapshots_and_approvals_preserved':True,
        'production_connection_used':False,'canonical_report_writes':0,'visual_acceptance':'UNVERIFIED','numerical_acceptance':'UNVERIFIED',
        'governed_regeneration_tested':False,'g04_complete':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_ALL_REPORTS_ERROR '+json.dumps({'type':type(error).__name__,'stage':STAGE,'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
