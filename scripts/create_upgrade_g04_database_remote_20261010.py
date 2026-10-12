"""Create one test database inside the approved recovery server; never drop/alter source."""
import hashlib,ipaddress,json,os,socket
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
SOURCE_HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
SOURCE_DB='ibkr_staging';TEST_DB='upgrade_g04_20261010'
TENANT='00000000-0000-0000-0000-000000000001'


def connect(database,readonly=True,autocommit=False):
    uri=urlsplit(os.environ['DATABASE_ADMIN_URL'])
    if uri.scheme not in ('postgres','postgresql') or uri.hostname!=SOURCE_HOST or uri.path!='/'+SOURCE_DB or uri.fragment or database not in (SOURCE_DB,TEST_DB,'postgres'):
        raise ValueError('Exact isolated database boundary required')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep:raise ValueError('Missing original credential reference')
    redirected=urlunsplit((uri.scheme,userinfo+'@'+HOST+(':'+str(uri.port) if uri.port else ''),'/'+database,uri.query,''))
    return psycopg.connect(redirected,host=HOST,port=5432,dbname=database,row_factory=dict_row,connect_timeout=15,
        sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',autocommit=autocommit,
        options='-c default_transaction_read_only='+('on' if readonly else 'off')+' -c statement_timeout=120000 -c app.tenant_id='+TENANT)


def fingerprint(database):
    with connect(database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        identity=db.execute("SELECT current_user AS role,current_database() AS database,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkrstgadmin','database':database,'ro':'on','ssl':True}:raise ValueError('Read-only identity differs')
        names=[r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
        if not 80<=len(names)<=120:raise ValueError('Unexpected recovery schema size')
        tables={}
        for name in names:
            digest=hashlib.sha256();count=0
            with db.cursor(name='g04_fingerprint') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier('public',name)))
                for row in cursor:digest.update((row['digest']+'\n').encode());count+=1
            tables[name]={'rows':count,'sha256':digest.hexdigest()}
        return tables


def main():
    addresses={r[4][0] for r in socket.getaddrinfo(HOST,5432,type=socket.SOCK_STREAM)}
    if addresses!={'10.50.11.4'}:raise ValueError('Exact private recovery endpoint required')
    with connect('postgres') as db:
        if db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(TEST_DB,)).fetchone():raise ValueError('Test database already exists; reconcile without overwriting')
        if db.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE datname=%s',(SOURCE_DB,)).fetchone()['n']!=0:raise ValueError('Recovery source has active callers; do not interrupt them')
    before=fingerprint(SOURCE_DB)
    for name,count in EXPECTED_COUNTS.items():
        if before[name]['rows']!=count:raise ValueError('Recovery report state drift')
    with connect('postgres',readonly=False,autocommit=True) as db:
        if db.execute('SELECT 1 FROM pg_database WHERE datname=%s',(TEST_DB,)).fetchone():raise ValueError('Concurrent test database creation')
        if db.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE datname=%s',(SOURCE_DB,)).fetchone()['n']!=0:raise ValueError('Source became busy')
        db.execute(sql.SQL('CREATE DATABASE {} WITH TEMPLATE {} OWNER {}').format(sql.Identifier(TEST_DB),sql.Identifier(SOURCE_DB),sql.Identifier('ibkrstgadmin')))
        db.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(TEST_DB),sql.Literal('Isolated upgrade G04 test copy on recovery server; never production. Created 2026-10-10.')))
    copied=fingerprint(TEST_DB);after=fingerprint(SOURCE_DB)
    if copied!=before or after!=before:raise ValueError('Source or test-copy fingerprints differ; preserve both for investigation')
    print('UPGRADE_G04_DATABASE '+json.dumps({'passed':True,'host':HOST,'database':TEST_DB,
        'source_database':SOURCE_DB,'tables':copied,'source_unchanged':True,'copy_identical':True,
        'production_connection_used':False,'new_databases':1,'private_data_uploaded_to_build_service':False}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_G04_DATABASE_ERROR '+json.dumps({'type':type(error).__name__,'sqlstate':getattr(error,'sqlstate',None),'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
