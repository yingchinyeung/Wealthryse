"""Use the installed reporting worker only against the isolated QA database."""
import contextlib, hashlib, io, json, os, socket, sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
SOURCE='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE='upgrade_g04_20261010'
TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-t'
PERIOD='2026-09-30'
CONTAINER='upgrade-g04-reports-20261010'


def isolated_uri(value):
    uri=urlsplit(value)
    if uri.scheme not in ('postgres','postgresql') or uri.hostname!=SOURCE or uri.path!='/ibkr_staging' or uri.fragment:
        raise ValueError('Unexpected original database reference')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep or not userinfo:raise ValueError('Missing original credential reference')
    # Discard every original query option: host/hostaddr/service may redirect libpq.
    return urlunsplit((uri.scheme,userinfo+'@'+HOST+':5432','/'+DATABASE,
        'sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt&connect_timeout=15',''))


def metadata(connection):
    rows=connection.execute('''SELECT r.generation_run_id,r.report_type,r.status,r.template_id,
        r.template_version,r.output_reference,r.output_sha256,s.snapshot_id,s.data_sha256
        FROM report_generation_runs r JOIN reporting_snapshots s
          ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
        WHERE r.tenant_id=? AND s.subject_id=? AND s.period_end=?
        ORDER BY r.generation_run_id''',(TENANT,SUBJECT,PERIOD)).fetchall()
    return [dict(r) for r in rows]


def main():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    if os.getuid()!=10001:raise ValueError('Non-root runtime required')
    if {r[4][0] for r in socket.getaddrinfo(HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:
        raise ValueError('Exact recovery endpoint required')
    worker_path=Path('/app/worker/generate_month_end_package.py')
    if hashlib.sha256(worker_path.read_text(encoding='utf-8').encode()).hexdigest()!=EXPECTED_WORKER_SHA:
        raise ValueError('Installed normal worker source differs')
    os.environ['DATABASE_URL']=isolated_uri(os.environ['DATABASE_URL'])
    os.environ.update(PILOT_ACCOUNT_ID=SUBJECT,REPORT_PERIOD_END=PERIOD,INTERNAL_REPORT_CONTAINER=CONTAINER)
    for key in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256','REPORT_DESIGN_VERSION',
                'REPORT_BOUND_SNAPSHOT_ID','REPORT_BOUND_SNAPSHOT_SHA256','REPORTING_PERIOD_START'):
        os.environ.pop(key,None)
    original_connect=psycopg.connect
    def guarded_connect(conninfo='',**kwargs):
        resolved=conninfo_to_dict(conninfo,**kwargs)
        if resolved.get('host')!=HOST or resolved.get('dbname')!=DATABASE or resolved.get('hostaddr') or resolved.get('service'):
            raise ValueError('Non-QA connection prohibited')
        kwargs.update(host=HOST,port=5432,dbname=DATABASE,sslmode='verify-full',
            sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c statement_timeout=120000 -c app.tenant_id='+TENANT)
        return original_connect(conninfo,**kwargs)
    psycopg.connect=guarded_connect
    sys.path[:0]=['/app/src','/app/scripts','/app/worker']
    import generate_month_end_package as worker
    from ibkr_etl.db import connect
    def open_db():return connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],TENANT)
    db=open_db()
    try:
        identity=dict(db.execute("SELECT current_user AS role,current_database() AS database,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone())
        if identity!={'role':'ibkr_stg_app','database':DATABASE,'ssl':True}:raise ValueError('QA app identity differs')
        records=metadata(db)
        snapshot=worker.active_month_end_snapshot_id(db,tenant_id=TENANT,subject_id=SUBJECT,period_end=PERIOD)
        if not snapshot:raise ValueError('QA frozen input unavailable')
        payload,digest=worker._load_frozen_snapshot(db,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=snapshot)
        if payload.get('quality_status')=='fail' or not (payload.get('monthly_reporting') or {}).get('available'):
            raise ValueError('Actual QA input is not ready')
        counts={t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in
            ('report_generation_runs','reporting_snapshots','report_artifact_releases','report_release_decisions','audit_events')}
        if MODE!='preflight':raise ValueError('Unknown mode')
        worker._acquire_package_lock(db,TENANT,SUBJECT,PERIOD)
        db.commit()  # This must not release the session-scoped lock.
        second=open_db()
        try:
            try:worker._acquire_package_lock(second,TENANT,SUBJECT,PERIOD)
            except RuntimeError as error:
                if str(error)!='Another report operation is active for this account and month':raise
            else:raise ValueError('Concurrent operation was not rejected')
        finally:second.close()
        db.close();db=open_db()
        worker._acquire_package_lock(db,TENANT,SUBJECT,PERIOD)
        db.close();db=open_db()
        os.environ['REPORT_DESIGN_VERSION']='qualification-negative-only'
        try:
            try:
                with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):worker.main()
            except RuntimeError as error:
                if str(error)!='Governed design regeneration requires its separately qualified worker':raise
            else:raise ValueError('Normal worker accepted governed regeneration')
        finally:os.environ.pop('REPORT_DESIGN_VERSION',None)
        after={t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in counts}
        if counts!=after or records!=metadata(db):raise ValueError('Read-only rehearsal changed QA report state')
        result={'passed':True,'mode':MODE,'host':HOST,'database':DATABASE,'identity':identity,
            'counts':counts,'records':records,'snapshot_id':snapshot,'snapshot_sha256':digest,
            'concurrency_rejected_across_commit':True,'lock_recovered_on_close':True,
            'governed_request_rejected_by_normal_worker':True,'qa_report_state_unchanged':True,
            'production_connection_used':False,'reports_published':False,'fresh_generation_tested':False}
    finally:db.close()
    print('UPGRADE_G04_WORKER '+json.dumps(result,default=str),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_G04_WORKER_ERROR '+json.dumps({'type':type(error).__name__,
            'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
