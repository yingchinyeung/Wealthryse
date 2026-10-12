"""Read-only all-subject inputs and report-metadata dependencies on QA only."""
import hashlib,json,os,socket
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg.rows import dict_row

HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
SOURCE='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DB='upgrade_g04_20261010';TENANT='00000000-0000-0000-0000-000000000001'
SUBJECTS={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}

def main():
    uri=urlsplit(os.environ['DATABASE_URL'])
    if uri.scheme not in ('postgres','postgresql') or uri.hostname!=SOURCE or uri.path!='/ibkr_staging' or uri.fragment:raise ValueError('Unexpected original database reference')
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep or not userinfo:raise ValueError('Credential reference missing')
    if {r[4][0] for r in socket.getaddrinfo(HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Exact QA private endpoint required')
    target=urlunsplit((uri.scheme,userinfo+'@'+HOST+':5432','/'+DB,'',''))
    with psycopg.connect(target,host=HOST,port=5432,dbname=DB,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',
            connect_timeout=15,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=60000 -c app.tenant_id='+TENANT) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        identity=conn.execute("SELECT current_user AS role,current_database() AS database,current_setting('transaction_read_only') AS readonly,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkr_stg_app','database':DB,'readonly':'on','ssl':True}:raise ValueError('Read-only QA identity differs')
        subjects=conn.execute('SELECT subject_id,lifecycle_status,reporting_currency,history_start_date FROM platform_subjects ORDER BY subject_id').fetchall()
        if {r['subject_id'] for r in subjects}!=SUBJECTS:raise ValueError('Unexpected subject set')
        checks=[]
        for subject in subjects:
            sid=subject['subject_id']
            rows=conn.execute("SELECT snapshot_id,data_sha256,snapshot_payload_json,quality_status,reporting_currency FROM reporting_snapshots WHERE tenant_id=%s AND subject_id=%s AND period_end='2026-09-30' AND status='frozen'",(TENANT,sid)).fetchall()
            if len(rows)!=1:raise ValueError('Expected one frozen September input per subject')
            row=rows[0];raw=row['snapshot_payload_json']
            if not isinstance(raw,str) or hashlib.sha256(raw.encode()).hexdigest()!=row['data_sha256']:raise ValueError('Snapshot integrity mismatch')
            payload=json.loads(raw)
            checks.append({'subject':sid,'currency':subject['reporting_currency'],'lifecycle':subject['lifecycle_status'],
                           'snapshot_id':str(row['snapshot_id']),'snapshot_sha256':row['data_sha256'],
                           'quality':row['quality_status'],'monthly_reporting_available':bool((payload.get('monthly_reporting') or {}).get('available')),
                           'currency_matches':row['reporting_currency']==subject['reporting_currency'],
                           'registered_reports':conn.execute('SELECT count(*) AS n FROM report_generation_runs WHERE tenant_id=%s AND snapshot_id=%s',(TENANT,row['snapshot_id'])).fetchone()['n']})
        edges=conn.execute("""SELECT parent.relname AS parent,child.relname AS child,con.conname AS constraint_name
            FROM pg_constraint con JOIN pg_class parent ON parent.oid=con.confrelid
            JOIN pg_class child ON child.oid=con.conrelid JOIN pg_namespace pn ON pn.oid=parent.relnamespace
            JOIN pg_namespace cn ON cn.oid=child.relnamespace
            WHERE con.contype='f' AND pn.nspname='public' AND cn.nspname='public' ORDER BY 1,2,3""").fetchall()
        closure={'reporting_snapshots'}
        for _ in range(100):
            expanded=closure|{r['child'] for r in edges if r['parent'] in closure}
            if expanded==closure:break
            closure=expanded
        else:raise ValueError('Dependency graph exceeds bound')
        affected=[r for r in edges if r['parent'] in closure]
        counts={}
        from psycopg import sql
        for name in sorted(closure):
            counts[name]=conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier('public',name))).fetchone()['n']
    print('UPGRADE_REPORTING_SCOPE '+json.dumps({'passed':True,'identity':identity,'subjects':checks,'report_metadata_dependency_closure':sorted(closure),
        'dependency_edges':affected,'row_counts':counts,'database_writes':0,'production_connection_used':False,
        'fresh_snapshot_creation_tested':False,'governed_regeneration_tested':False,'g04_complete':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('UPGRADE_REPORTING_SCOPE_ERROR '+json.dumps({'type':type(error).__name__,'sqlstate':getattr(error,'sqlstate',None)}),flush=True)
        raise SystemExit(1)
