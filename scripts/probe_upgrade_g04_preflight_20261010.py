"""SELECT-only feasibility review on the existing private recovery server."""
import hashlib, ipaddress, json, os, socket
from urllib.parse import urlsplit, urlunsplit
import psycopg
from psycopg.rows import dict_row

SOURCE='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
TARGET='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
TEST_DATABASE='upgrade_g04_20261010'


def redirect(value):
    uri=urlsplit(value)
    if uri.hostname!=SOURCE or uri.scheme not in ('postgres','postgresql'):
        raise ValueError('Exact source connection reference required')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep or not userinfo or uri.fragment:
        raise ValueError('Unexpected connection form')
    return urlunsplit((uri.scheme,userinfo+'@'+TARGET+(':'+str(uri.port) if uri.port else ''),uri.path,uri.query,''))


def main():
    addresses=sorted({r[4][0] for r in socket.getaddrinfo(TARGET,5432,type=socket.SOCK_STREAM)})
    if not addresses or any(ipaddress.ip_address(a) not in ipaddress.ip_network('10.50.11.0/27') for a in addresses):
        raise ValueError('Recovery-only private subnet required')
    uri=redirect(os.environ['DATABASE_ADMIN_URL'])
    with psycopg.connect(uri,row_factory=dict_row,connect_timeout=15,sslmode='verify-full',
            sslrootcert='/etc/ssl/certs/ca-certificates.crt',
            options='-c default_transaction_read_only=on -c statement_timeout=30000 -c app.tenant_id=00000000-0000-0000-0000-000000000001') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        identity=db.execute("SELECT current_user AS role,current_database() AS database,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity['ro']!='on' or identity['ssl'] is not True:
            raise ValueError('Verified TLS and read-only transaction required')
        can_create=db.execute('SELECT rolcreatedb FROM pg_roles WHERE rolname=current_user').fetchone()['rolcreatedb']
        exists=db.execute('SELECT datname FROM pg_database WHERE datname=%s',(TEST_DATABASE,)).fetchone() is not None
        subjects=db.execute('SELECT subject_id,lifecycle_status,reporting_currency FROM platform_subjects ORDER BY subject_id').fetchall()
        schema=db.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        counts={}
        for table in ('reporting_snapshots','report_generation_runs','report_artifact_releases','report_release_decisions'):
            from psycopg import sql
            counts[table]=db.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
    print('UPGRADE_G04_PREFLIGHT '+json.dumps({'passed':True,'host':TARGET,'addresses':addresses,
        'database':identity['database'],'role':identity['role'],'can_create_database':can_create,
        'test_database_exists':exists,'subjects':subjects,'table_counts':counts,
        'schema_sha256':hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest(),
        'database_writes':0,'production_connection_used':False,'verified_tls':True,
        'database_readonly':True,'private_data_uploaded_to_build_service':False}),flush=True)


if __name__=='__main__':main()
