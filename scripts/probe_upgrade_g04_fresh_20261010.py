"""Actual normal-worker generation, injected upload failure, and retry in QA only."""
import contextlib,hashlib,io,json,os,socket,sys,zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
import qa_boundary as q
STAGE='initialization'


class PlannedUploadFailure(RuntimeError):pass


def main():
    global STAGE
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    if os.getuid()!=10001:raise ValueError('Non-root required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Unexpected recovery IP')
    source=Path('/app/scripts/generate_month_end_package.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Installed worker changed')
    os.environ['DATABASE_URL']=q.isolated_uri(os.environ['DATABASE_URL'])
    os.environ.update(PILOT_ACCOUNT_ID=q.SUBJECT,REPORT_PERIOD_END=q.PERIOD,INTERNAL_REPORT_CONTAINER=q.CONTAINER)
    for key in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256','REPORT_DESIGN_VERSION','REPORT_BOUND_SNAPSHOT_ID','REPORT_BOUND_SNAPSHOT_SHA256','REPORTING_PERIOD_START'):
        os.environ.pop(key,None)
    original_connect=psycopg.connect
    def guarded_connect(conninfo='',**kwargs):
        resolved=conninfo_to_dict(conninfo,**kwargs)
        if resolved.get('host')!=q.HOST or resolved.get('dbname')!=q.DATABASE or resolved.get('hostaddr') or resolved.get('service'):
            raise ValueError('Connection outside QA forbidden')
        kwargs.update(host=q.HOST,port=5432,dbname=q.DATABASE,sslmode='verify-full',
            sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c statement_timeout=120000 -c app.tenant_id='+q.TENANT)
        return original_connect(conninfo,**kwargs)
    psycopg.connect=guarded_connect
    sys.path[:0]=['/app/src','/app/scripts']
    import generate_month_end_package as worker
    from ibkr_etl.db import connect
    def db():return connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],q.TENANT)
    storage=os.environ['AZURE_STORAGE_ACCOUNT_URL']
    if storage!='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net':raise ValueError('Unexpected storage')
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
    client=BlobServiceClient(storage,credential=credential)
    bucket=client.get_container_client(q.CONTAINER)
    if bucket.get_container_properties().get('public_access') is not None or list(bucket.list_blobs(results_per_page=1)):
        raise ValueError('New empty private QA output container required')

    def fingerprint(connection,table):
        # Only fixed internal table identifiers, never user SQL.
        allowed={'report_artifact_releases','report_release_decisions','reporting_snapshots','audit_events'}
        if table not in allowed:raise ValueError('Fingerprint scope')
        rows=connection.execute('SELECT md5(row_to_json(t)::text) AS hash FROM '+table+' t ORDER BY 1').fetchall()
        return hashlib.sha256(('\n'.join(r['hash'] for r in rows)).encode()).hexdigest()
    STAGE='qa-baseline-check';connection=db()
    try:
        identity=dict(connection.execute("SELECT current_user AS role,current_database() AS database,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone())
        if identity!={'role':'ibkr_stg_app','database':q.DATABASE,'ssl':True}:raise ValueError('QA identity differs')
        records=q.metadata(connection)
        if json.loads(json.dumps(records,default=str))!=EXPECTED_RECORDS:raise ValueError('QA baseline changed')
        snapshot=worker.active_month_end_snapshot_id(connection,tenant_id=q.TENANT,subject_id=q.SUBJECT,period_end=q.PERIOD)
        if str(snapshot)!=EXPECTED_SNAPSHOT:raise ValueError('QA snapshot changed')
        latest={}
        for r in reversed(records):
            if str(r['snapshot_id'])==EXPECTED_SNAPSHOT:latest.setdefault(r['report_type'],r)
        if set(latest)!={'factsheet','presentation'}:raise ValueError('Two QA report kinds required')
        target=list(latest.values())
        if not target or len(target)>12 or any(r['status'] not in ('awaiting_approval','failed') for r in target):raise ValueError('Unreleased QA records only')
        target_ids=[r['generation_run_id'] for r in target]
        released=connection.execute('SELECT count(*) AS n FROM report_artifact_releases WHERE generation_run_id = ANY(?)',(target_ids,)).fetchone()['n']
        if released:raise ValueError('Do not reset copied release records')
        preserved={t:fingerprint(connection,t) for t in ('report_artifact_releases','report_release_decisions','reporting_snapshots')}
        worker._acquire_package_lock(connection,q.TENANT,q.SUBJECT,q.PERIOD)
        for record in target:
            changed=connection.execute("UPDATE report_generation_runs SET status='failed',error_message='Isolated QA fresh-generation trial; production unchanged' WHERE generation_run_id=? AND tenant_id=? AND status=?",
                (record['generation_run_id'],q.TENANT,record['status'])).rowcount
            if changed!=1:raise ValueError('QA reset race')
        connection.commit();STAGE='qa-latest-records-prepared'
        reset=q.metadata(connection);audit_before=fingerprint(connection,'audit_events')
    finally:connection.close()

    original_upload=worker.upload_bytes;original_download=worker.download_blob_bytes
    uploads=[]
    def scoped_upload(url,container,name,content,**kwargs):
        if url!=storage or container!=q.CONTAINER or not name.startswith('review/'+q.SUBJECT+'/'+q.PERIOD+'/') or kwargs.get('overwrite') is not False:
            raise ValueError('Non-QA or mutable upload forbidden')
        if any(p in ('.','..','') for p in name.split('/')):raise ValueError('Invalid blob path')
        result=original_upload(url,container,name,content,**kwargs)
        uploads.append({'name':name,'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)})
        return result
    fail_count=0
    def fail_second(*args,**kwargs):
        nonlocal fail_count
        fail_count+=1
        if fail_count==2:raise PlannedUploadFailure('Planned QA-only second upload failure')
        return scoped_upload(*args,**kwargs)
    def run():
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            status=worker.main()
        if status:raise ValueError('Worker failed')
        # Normal renderer may print progress; select the final normal-worker JSON.
        objects=[]
        for line in output.getvalue().splitlines():
            try:value=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(value,dict) and value.get('status') in ('generated','already_generated'):objects.append(value)
        if len(objects)!=1:raise ValueError('Missing unique worker outcome')
        return objects[0]
    STAGE='injected-upload-failure'
    with patch.object(worker,'upload_bytes',fail_second):
        try:run()
        except PlannedUploadFailure:pass
        else:raise ValueError('Injected upload failure not reached')
    if fail_count!=2 or len(uploads)!=1:raise ValueError('Wrong failure point')
    connection=db()
    try:
        if q.metadata(connection)!=reset or fingerprint(connection,'audit_events')!=audit_before:
            raise ValueError('Partial failed package registered')
        if any(fingerprint(connection,t)!=value for t,value in preserved.items()):raise ValueError('Approval or snapshot state changed')
    finally:connection.close()
    STAGE='fresh-generation-recovery'
    with patch.object(worker,'upload_bytes',scoped_upload):result=run()
    if result.get('status')!='generated' or result.get('snapshot_action')!='reused_existing':raise ValueError('Fresh artifact generation not exercised')
    def prohibited(*args,**kwargs):raise ValueError('Idempotent replay attempted new generation')
    STAGE='idempotent-replay'
    with patch.object(worker,'upload_bytes',prohibited),patch.object(worker,'generate_month_end_commentary',prohibited),patch.object(worker,'render_factsheet',prohibited):
        replay=run()
    if replay.get('status')!='already_generated':raise ValueError('Retry not idempotent')
    STAGE='artifact-readback';connection=db()
    try:
        if any(fingerprint(connection,t)!=value for t,value in preserved.items()):raise ValueError('Approval or snapshot state changed')
        final=q.metadata(connection);artifacts=[]
        for kind,run_id in result['generation_run_ids'].items():
            row=next(r for r in final if r['generation_run_id']==run_id)
            if row['status']!='awaiting_approval' or not row['output_reference'].startswith(q.CONTAINER+'/'):raise ValueError('QA output escaped review boundary')
            content=original_download(storage,q.CONTAINER,row['output_reference'].split('/',1)[1],maximum_bytes=64*1024*1024)
            if hashlib.sha256(content).hexdigest()!=row['output_sha256']:raise ValueError('Readback differs')
            if kind=='factsheet':
                from pypdf import PdfReader
                pages=len(PdfReader(io.BytesIO(content)).pages)
                if pages!=2:raise ValueError('Expected two PDF pages')
                count=pages
            else:
                import re
                with zipfile.ZipFile(io.BytesIO(content)) as z:count=sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml',n)) for n in z.namelist())
                if count!=20:raise ValueError('Expected twenty slides')
            artifacts.append({'report_type':kind,'generation_run_id':run_id,'output_reference':row['output_reference'],'sha256':row['output_sha256'],'pages_or_slides':count})
    finally:connection.close();client.close();credential.close()
    print('UPGRADE_G04_FRESH '+json.dumps({'passed':True,'database':q.DATABASE,'host':q.HOST,
        'subject':q.SUBJECT,'period_end':q.PERIOD,'snapshot_id':EXPECTED_SNAPSHOT,
        'fresh_artifact_generation':True,'fresh_snapshot_creation':False,'injected_upload_failure_rolled_back':True,
        'idempotent_replay_passed':True,'approvals_and_snapshots_unchanged':True,'artifacts':artifacts,
        'production_connection_used':False,'canonical_report_container_writes':0,'saved_job_changed':False,
        'visual_acceptance':'UNVERIFIED','g04_complete':False}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_G04_FRESH_ERROR '+json.dumps({'type':type(error).__name__,'stage':STAGE,'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
