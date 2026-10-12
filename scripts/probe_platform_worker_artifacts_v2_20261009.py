"""Read-only current-month artifact checks and synthetic session-lock proof."""
import ast,hashlib,json,os,sys,uuid,time
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.cloud_storage import download_blob_bytes
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id

def main():
    namespace={'hashlib':hashlib,'download_blob_bytes':download_blob_bytes}
    names={'_acquire_package_lock','_verify_existing_artifacts','_sha256'}
    tree=ast.parse(WORKER_SOURCE)
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<candidate>','exec'),namespace)
    tenant='00000000-0000-0000-0000-000000000001'
    kwargs=dict(row_factory=_hybrid_row_factory,connect_timeout=20,options='-c default_transaction_read_only=on -c statement_timeout=20000')
    def raw_connection():return psycopg.connect(os.environ['DATABASE_URL'],**kwargs)
    synthetic='upgrade-synthetic-'+str(uuid.uuid4())
    checks=[]
    with raw_connection() as first,raw_connection() as second:
        a,b=PostgresConnection(first),PostgresConnection(second)
        namespace['_acquire_package_lock'](a,tenant,synthetic,'2026-09-30')
        first.commit()
        try:namespace['_acquire_package_lock'](b,tenant,synthetic,'2026-09-30')
        except RuntimeError:checks.append({'name':'second_session_blocked_after_snapshot_commit','passed':True})
        else:raise AssertionError('Session lock failed')
        first.close()
        # Client close sends Terminate; server processing is asynchronous.
        # Bound the release check rather than assuming same-instant delivery.
        began=time.monotonic()
        while True:
            try:
                namespace['_acquire_package_lock'](b,tenant,synthetic,'2026-09-30')
                break
            except RuntimeError:
                if time.monotonic()-began>5:raise
                time.sleep(.1)
        checks.append({'name':'lock_released_on_connection_close','passed':True,'release_seconds':time.monotonic()-began})
    records=[]
    with raw_connection() as raw:
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant,))
        conn=PostgresConnection(raw)
        role=raw.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if role['rolsuper'] or role['rolbypassrls']:raise ValueError('Application identity required')
        subjects=raw.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id",(tenant,)).fetchall()
        for row in subjects:
            subject=row['subject_id']
            snapshot=active_month_end_snapshot_id(conn,tenant_id=tenant,subject_id=subject,period_end='2026-09-30')
            if snapshot is None:
                records.append({'subject':subject,'period':'2026-09-30','status':'missing_active_snapshot'})
                continue
            try:
                namespace['_verify_existing_artifacts'](conn,tenant_id=tenant,account_id=subject,snapshot_id=snapshot,
                    storage_url='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',container='internal-reports')
                records.append({'subject':subject,'period':'2026-09-30','status':'both_artifact_hashes_verified'})
            except Exception as error:
                records.append({'subject':subject,'period':'2026-09-30','status':'failed','error_class':type(error).__name__})
        if raw.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']!='on':raise ValueError('Read-only guard failed')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'worker_sha256':hashlib.sha256(WORKER_SOURCE.encode()).hexdigest(),
        'session_lock_checks':checks,'artifact_checks':records,'persistent_database_writes':0,'blob_writes':0,
        'model_calls':0,'report_approvals_changed':False,'all_artifacts_verified':all(r['status']=='both_artifact_hashes_verified' for r in records)}),flush=True)

if __name__=='__main__':main()
