"""Installed snapshot creation and deterministic replay, confined to fresh QA."""
import hashlib,json,os,socket,sys
from pathlib import Path
from datetime import date
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg.conninfo import conninfo_to_dict

HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com';SOURCE='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE='upgrade_g04_fresh_20261011';TENANT='00000000-0000-0000-0000-000000000001';PERIOD='2026-09-30'
SUBJECTS={'alpha-world-ms':'HKD','alpha-world-t':'HKD','hong-kong-china':'USD','U16088571':'USD','U16088606':'USD'}
STAGE='initialization'

def main():
    global STAGE
    if os.getuid()!=10001:raise ValueError('Non-root required')
    if {r[4][0] for r in socket.getaddrinfo(HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Unexpected recovery endpoint')
    uri=urlsplit(os.environ['DATABASE_URL'])
    if uri.hostname!=SOURCE or uri.scheme not in ('postgres','postgresql') or uri.path!='/ibkr_staging' or uri.fragment:raise ValueError('Original connection reference differs')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep:raise ValueError('Credential reference missing')
    target=urlunsplit((uri.scheme,userinfo+'@'+HOST+':5432','/'+DATABASE,'',''))
    original_connect=psycopg.connect
    def guarded(conninfo='',**kwargs):
        options=conninfo_to_dict(conninfo,**kwargs)
        if options.get('host')!=HOST or options.get('dbname')!=DATABASE or options.get('hostaddr') or options.get('service'):raise ValueError('Non-QA connection forbidden')
        kwargs.update(host=HOST,port=5432,dbname=DATABASE,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
                      options='-c statement_timeout=120000 -c app.tenant_id='+TENANT)
        return original_connect(conninfo,**kwargs)
    psycopg.connect=guarded;os.environ['DATABASE_URL']=target
    sys.path[:0]=['/app/src','/app/scripts']
    import generate_month_end_package as worker
    if hashlib.sha256(Path('/app/scripts/generate_month_end_package.py').read_text(encoding='utf-8').encode()).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Installed worker source changed')
    from ibkr_etl.db import connect
    db=connect(Path('/tmp/unused.sqlite3'),target,TENANT)
    results=[]
    try:
        identity=dict(db.execute('SELECT current_user AS role,current_database() AS database,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone())
        if identity!={'role':'ibkr_stg_app','database':DATABASE,'ssl':True}:raise ValueError('QA identity differs')
        if db.execute('SELECT count(*) AS n FROM reporting_snapshots').fetchone()['n']!=0:raise ValueError('Fresh empty snapshot registry required')
        for subject,currency in SUBJECTS.items():
            STAGE='create:'+subject
            row=db.execute('SELECT history_start_date,reporting_currency,lifecycle_status FROM platform_subjects WHERE subject_id=?',(subject,)).fetchone()
            if row is None or row['reporting_currency']!=currency or row['lifecycle_status']!='active':raise ValueError('Subject contract differs')
            worker._acquire_package_lock(db,TENANT,subject,PERIOD)
            worker._require_final_source_import(db,subject,date.fromisoformat(PERIOD))
            args=dict(tenant_id=TENANT,subject_id=subject,period_start=str(row['history_start_date']),period_end=PERIOD,reporting_currency=currency,reject_quality_fail=True)
            fresh=worker.freeze_month_end_snapshot(db,**args)
            if not fresh.inserted:raise ValueError('New snapshot not created')
            payload,digest=worker._load_frozen_snapshot(db,tenant_id=TENANT,account_id=subject,snapshot_id=fresh.snapshot_id)
            if digest!=fresh.data_sha256 or payload['reporting_currency']!=currency or payload.get('quality_status')=='fail' or not (payload.get('monthly_reporting') or {}).get('available'):raise ValueError('New snapshot input failed acceptance')
            STAGE='replay:'+subject;replayed=worker.freeze_month_end_snapshot(db,**args)
            if replayed.inserted or replayed.snapshot_id!=fresh.snapshot_id or replayed.data_sha256!=fresh.data_sha256:raise ValueError('Unchanged replay not deterministic')
            results.append({'subject':subject,'currency':currency,'snapshot_id':str(fresh.snapshot_id),'sha256':digest,'quality':fresh.quality_status,'created_new':True,'unchanged_replay_passed':True})
            print('UPGRADE_FRESH_SNAPSHOT_PROGRESS '+json.dumps(results[-1]),flush=True)
        if db.execute('SELECT count(*) AS n FROM reporting_snapshots').fetchone()['n']!=5:raise ValueError('Expected exactly five new snapshots')
        for table in ('report_generation_runs','report_artifact_releases','report_release_decisions'):
            if db.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']:raise ValueError('Unexpected report generation or approval')
    finally:db.close()
    print('UPGRADE_FRESH_SNAPSHOTS '+json.dumps({'passed':True,'database':DATABASE,'host':HOST,'identity':identity,'subjects':results,
        'fresh_snapshot_creation_tested':True,'unchanged_replay_passed':True,'reports_generated':0,'reports_published':0,
        'production_connection_used':False,'governed_regeneration_tested':False,'g04_complete':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_FRESH_SNAPSHOTS_ERROR '+json.dumps({'type':type(error).__name__,'stage':STAGE,'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
