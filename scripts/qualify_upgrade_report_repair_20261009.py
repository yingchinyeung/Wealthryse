"""Hash-verified original copies and clone-only SQL repair/rollback qualification."""
import os,sys,json,hashlib
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from azure.core.exceptions import ResourceExistsError
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.util import canonical_json
from ibkr_etl.monthly_reports import validate_review_artifact

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def clone_url(value):
    p=urlsplit(value)
    if p.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected source database')
    user,separator,_=p.netloc.rpartition('@')
    if not separator:raise ValueError('Existing URI authority required')
    return urlunsplit((p.scheme,user+'@ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))

def main():
    if len(REPAIRS)!=375 or len({r['generation_run_id'] for r in REPAIRS})!=375:raise ValueError('Exact repair scope required')
    packages=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=120000') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        for repair in REPAIRS:
            row=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s',(web.TENANT_ID,repair['generation_run_id'])).fetchone()['record']
            if row['status']!='awaiting_approval' or row['output_reference']!=repair['output_reference'] or row['output_sha256']!=repair['output_sha256']:raise ValueError('Original report row drift')
            if db.execute('SELECT 1 FROM report_artifact_releases WHERE tenant_id=%s AND generation_run_id=%s LIMIT 1',(web.TENANT_ID,repair['generation_run_id'])).fetchone():raise ValueError('Repair would involve a released artifact')
            parent=db.execute('SELECT subject_id,period_end FROM reporting_snapshots WHERE tenant_id=%s AND snapshot_id=%s',(web.TENANT_ID,row['snapshot_id'])).fetchone()
            if parent['subject_id']!=repair['subject_id'] or str(parent['period_end'])!=repair['period_end']:raise ValueError('Parent scope differs')
            expected={**row,'output_reference':repair['recovered_reference']}
            validate_review_artifact({**expected,'subject_id':parent['subject_id']},account_id=parent['subject_id'],period_end=str(parent['period_end']),container='internal-reports')
            packages.append({'old_row':row,'after_row':expected,'source_version':repair['source_version']})
        fingerprints={}
        for table in ('reporting_snapshots','report_generation_runs','report_release_decisions','report_artifact_releases'):
            records=[r['record'] for r in db.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s',(web.TENANT_ID,)).fetchall()]
            fingerprints[table]={'rows':len(records),'sha256':sha(sorted(records,key=canonical_json))}
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    container=service.get_container_client('internal-reports');copied={}
    for item in packages:
        original=item['old_row'];target=item['after_row']['output_reference'].split('/',1)[1];digest=original['output_sha256']
        if target in copied:continue
        data=container.get_blob_client(original['output_reference'].split('/',1)[1],version_id=item['source_version']).download_blob().readall()
        if len(data)>100*1024*1024 or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Original version bytes differ')
        blob=container.get_blob_client(target)
        try:blob.upload_blob(data,overwrite=False,metadata={'sha256':digest,'purpose':'historical-byte-recovery'},content_settings=ContentSettings(content_type='application/pdf' if target.endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
        except ResourceExistsError:pass
        if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Immutable recovered copy differs')
        copied[target]={'sha256':digest,'version_id':blob.get_blob_properties().version_id}
    with psycopg.connect(clone_url(os.environ['DATABASE_URL']),row_factory=dict_row,options='-c statement_timeout=120000 -c lock_timeout=5000') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        db.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        before=[]
        for item in packages:
            old=item['old_row'];row=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s FOR UPDATE',(web.TENANT_ID,old['generation_run_id'])).fetchone()['record']
            if row!=old:raise ValueError('Restored original differs')
            before.append(row)
        db.execute('SAVEPOINT before_repair')
        for item in packages:
            old=item['old_row'];expected=item['after_row']
            count=db.execute('UPDATE report_generation_runs SET output_reference=%s WHERE tenant_id=%s AND generation_run_id=%s AND output_reference=%s AND output_sha256=%s AND status=%s',(expected['output_reference'],web.TENANT_ID,old['generation_run_id'],old['output_reference'],old['output_sha256'],'awaiting_approval')).rowcount
            row=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s',(web.TENANT_ID,old['generation_run_id'])).fetchone()['record']
            if count!=1 or row!=expected:raise ValueError('Clone-only update changed more than the reference')
        db.execute('ROLLBACK TO SAVEPOINT before_repair')
        for old in before:
            restored=db.execute('SELECT to_jsonb(r) AS record FROM report_generation_runs r WHERE tenant_id=%s AND generation_run_id=%s',(web.TENANT_ID,old['generation_run_id'])).fetchone()['record']
            if restored!=old:raise ValueError('Full-row rollback differs')
        db.rollback()
    value={'passed':True,'qualified':packages,'copied_blobs':copied,'report_fingerprints':fingerprints,'live_database_writes':0,'clone_update_and_rollback_passed':True,'released_artifacts_involved':0}
    data=canonical_json(value).encode();digest=hashlib.sha256(data).hexdigest();name='review/platform-upgrade-20261009/report-repair-qualified-'+digest+'.json'
    blob=container.get_blob_client(name);blob.upload_blob(data,overwrite=False)
    if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Recovery preimage evidence differs')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'qualified_rows':375,'copied_blobs':len(copied),'live_database_writes':0,'clone_rollback_passed':True,'private_blob':name,'sha256':digest}),flush=True)
if __name__=='__main__':main()
