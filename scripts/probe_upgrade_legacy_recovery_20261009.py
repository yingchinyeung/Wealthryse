"""Read-only full-table reconciliation of legacy source and isolated recovery."""
import os,json,hashlib,time
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
source=os.environ['DATABASE_URL'];p=urlsplit(source)
if p.hostname!='ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com':raise ValueError('Legacy source host differs')
credentials,separator,_=p.netloc.rpartition('@')
if not separator:raise ValueError('Existing private credential authority required')
target=urlunsplit((p.scheme,credentials+'@ibkrv11-upgrade-recovery-1009.postgres.database.azure.com'+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))
started=time.monotonic()
def fingerprint(uri):
    result={}
    with psycopg.connect(uri,row_factory=dict_row,connect_timeout=20) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        db.execute("SELECT set_config('app.tenant_id',%s,true)",(os.environ['PORTFOLIO_TENANT_ID'],))
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only transaction required')
        columns=db.execute("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        tables=[r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
        if not 1<=len(tables)<=200:raise ValueError('Legacy table scope outside bound')
        for name in tables:
            h=hashlib.sha256();count=0
            with db.cursor(name='legacy_recovery_readonly_rows') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier('public',name)))
                for row in cursor:h.update((row['digest']+'\n').encode());count+=1
            result[name]={'rows':count,'sha256':h.hexdigest()}
        schema=hashlib.sha256(json.dumps(columns,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    return {'schema_sha256':schema,'tables':result}
before=fingerprint(source);restored=fingerprint(target);after=fingerprint(source)
differences=[name for name in set(before['tables'])|set(restored['tables']) if before['tables'].get(name)!=restored['tables'].get(name)]
passed=before==after and before['schema_sha256']==restored['schema_sha256'] and not differences
print('LEGACY_RECOVERY_PROOF '+json.dumps({'passed':passed,'database_writes':0,'source_stable_during_probe':before==after,'table_differences':differences,'source':before,'restored':restored,'elapsed_seconds':round(time.monotonic()-started,3),'production_redirected':False}),flush=True)
if not passed:raise SystemExit(1)
