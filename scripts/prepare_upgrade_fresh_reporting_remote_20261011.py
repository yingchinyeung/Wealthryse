"""Create a second isolated QA copy with only copied report history cleared."""
import hashlib,json,os,socket
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com';SOURCE_HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
SOURCE_DB='ibkr_staging';TEST_DB='upgrade_g04_fresh_20261011';TENANT='00000000-0000-0000-0000-000000000001'
REPORT_TABLES=('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')

def connect(database,readonly=True,autocommit=False):
    uri=urlsplit(os.environ['DATABASE_ADMIN_URL'])
    if database not in (SOURCE_DB,TEST_DB,'postgres') or uri.hostname!=SOURCE_HOST or uri.path!='/'+SOURCE_DB or uri.scheme not in ('postgres','postgresql') or uri.fragment:raise ValueError('Exact recovery-copy boundary required')
    if not readonly and database not in (TEST_DB,'postgres'):raise ValueError('Source writes prohibited')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep or not userinfo:raise ValueError('Invalid credential reference')
    target=urlunsplit((uri.scheme,userinfo+'@'+HOST+':5432','/'+database,'',''))
    return psycopg.connect(target,host=HOST,port=5432,dbname=database,row_factory=dict_row,connect_timeout=15,sslmode='verify-full',
        sslrootcert='/etc/ssl/certs/ca-certificates.crt',autocommit=autocommit,
        options='-c default_transaction_read_only='+('on' if readonly else 'off')+' -c statement_timeout=120000 -c app.tenant_id='+TENANT)

def fingerprint(database):
    with connect(database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        names=[r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
        if len(names)!=97:raise ValueError('Schema drift')
        result={}
        for name in names:
            rows=db.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier('public',name))).fetchall()
            result[name]={'rows':len(rows),'sha256':hashlib.sha256(''.join(r['digest']+'\n' for r in rows).encode()).hexdigest()}
        return result

def main():
    if {r[4][0] for r in socket.getaddrinfo(HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong recovery IP')
    before=fingerprint(SOURCE_DB)
    if before!=EXPECTED_TABLES:raise ValueError('Recovery source drift')
    with connect('postgres',readonly=False,autocommit=True) as db:
        if db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(TEST_DB,)).fetchone():raise ValueError('Preserve existing test database')
        if db.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE datname=%s',(SOURCE_DB,)).fetchone()['n']:raise ValueError('Source is busy; never interrupt callers')
        db.execute(sql.SQL('CREATE DATABASE {} WITH TEMPLATE {} OWNER {}').format(sql.Identifier(TEST_DB),sql.Identifier(SOURCE_DB),sql.Identifier('ibkrstgadmin')))
        db.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(TEST_DB),sql.Literal('Isolated fresh-report qualification copy created 2026-10-11; never production.')))
    if fingerprint(TEST_DB)!=before:raise ValueError('QA copy mismatch; preserve for investigation')
    with connect(TEST_DB,readonly=False) as db:
        identity=db.execute('SELECT current_database() AS database').fetchone()['database']
        if identity!=TEST_DB:raise ValueError('QA reset escaped boundary')
        # RESTRICT, not CASCADE: unreviewed dependencies must stop this operation.
        db.execute(sql.SQL('TRUNCATE TABLE {} RESTRICT').format(sql.SQL(',').join(sql.Identifier('public',t) for t in REPORT_TABLES)))
    after=fingerprint(TEST_DB)
    if any(after[t]['rows'] for t in REPORT_TABLES) or any(after[t]!=before[t] for t in before if t not in REPORT_TABLES):raise ValueError('QA reset touched non-report data')
    if fingerprint(SOURCE_DB)!=before:raise ValueError('Source changed')
    print('UPGRADE_FRESH_REPORTING_DB '+json.dumps({'passed':True,'host':HOST,'database':TEST_DB,'source_database':SOURCE_DB,
        'source_unchanged':True,'tables_checked':97,'preserved_nonreport_tables':92,'cleared_copied_report_tables':list(REPORT_TABLES),
        'qa_fingerprints':after,'production_connection_used':False,'production_approvals_changed':False,'new_databases':1,
        'private_data_uploaded_to_build_service':False,'fresh_snapshot_creation_tested':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_FRESH_REPORTING_DB_ERROR '+json.dumps({'type':type(error).__name__,'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
