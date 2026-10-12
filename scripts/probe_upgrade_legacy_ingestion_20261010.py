"""Legacy ingestion replay solely on synthetic temporary recovery relations."""
import hashlib,json,os,socket,sys,tempfile
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.db import _hybrid_row_factory
from ibkr_etl.config import Settings
from ibkr_etl.service import ingest_content
from ibkr_etl.analyst_export import export_snapshot,build_csv_zip
SOURCE='ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com'
TARGET='ibkrv11-upgrade-recovery-1009.postgres.database.azure.com'
TENANT='00000000-0000-0000-0000-000000000001'
p=urlsplit(os.environ['DATABASE_URL'])
if p.hostname!=SOURCE or os.environ['PORTFOLIO_TENANT_ID']!=TENANT:raise ValueError('Exact source credential and tenant required')
auth,sep,_=p.netloc.rpartition('@')
if not sep:raise ValueError('Credential authority missing')
uri=urlunsplit((p.scheme,auth+'@'+TARGET+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))
os.environ.update(DATABASE_URL=uri,IBKR_RUN_MIGRATIONS='false')
raw=psycopg.connect(uri,row_factory=_hybrid_row_factory,connect_timeout=20)


def deny(*args,**kwargs):raise RuntimeError('No external connections permitted in legacy replay')


socket.socket.connect=socket.socket.connect_ex=socket.create_connection=deny
class Borrowed:
    def __getattr__(self,name):return getattr(raw,name)
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,typ,value,tb):raw.rollback() if typ else raw.commit()


def isolated_connect(conninfo='',**kwargs):
    expected=conninfo_to_dict(uri);actual=conninfo_to_dict(conninfo,**{k:v for k,v in kwargs.items() if k!='row_factory'})
    if any(actual.get(k)!=expected.get(k) for k in ('host','dbname','user')) or actual['host']!=TARGET:raise ValueError('No connection outside exact clone')
    return Borrowed()


psycopg.connect=isolated_connect
def fingerprints():
    raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    # Public-table RLS helper functions may resolve account lookups through
    # search_path. Temporary fixture accounts must not shadow those lookups.
    raw.execute('SET LOCAL search_path=public,pg_temp')
    if raw.execute("SELECT to_regclass('accounts')=to_regclass('public.accounts')").fetchone()[0] is not True:
        raise ValueError('Preservation RLS lookup must resolve persistent accounts')
    raw.execute("SET LOCAL TIME ZONE 'UTC'")
    raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
    names=[r[0] for r in raw.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
    if len(names)!=25:raise ValueError('Exact legacy clone schema required')
    result={}
    for name in names:
        h=hashlib.sha256();count=0
        with raw.cursor(name='legacy_security_preservation') as c:
            c.execute(sql.SQL('SELECT md5(row_to_json(t)::text) FROM {} t ORDER BY 1').format(sql.Identifier('public',name)))
            for r in c:h.update((r[0]+'\n').encode());count+=1
        result[name]={'rows':count,'sha256':h.hexdigest()}
    raw.commit();return result


checks=[]
try:
    raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,));raw.execute("SET TIME ZONE 'UTC'");raw.commit()
    before=fingerprints()
    for name in before:
        raw.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE {} INCLUDING ALL) ON COMMIT PRESERVE ROWS').format(sql.Identifier(name),sql.Identifier('public',name)))
        for column in raw.execute("SELECT column_name,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(name,)).fetchall():
            if str(column['column_default'] or '').startswith('nextval('):
                seq=name+'_'+column['column_name']+'_fixture'
                if len(seq)>63:raise ValueError('Fixture sequence outside bound')
                raw.execute(sql.SQL('CREATE TEMP SEQUENCE {}').format(sql.Identifier(seq)))
                raw.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET DEFAULT nextval({})').format(sql.Identifier('pg_temp',name),sql.Identifier(column['column_name']),sql.Literal('pg_temp.'+seq)))
    raw.execute('SET search_path=pg_temp,public')
    for name in before:
        if raw.execute('SELECT relpersistence FROM pg_class WHERE oid=to_regclass(%s)',(name,)).fetchone()[0]!='t':raise ValueError('Non-temporary target')
    raw.execute('SET default_transaction_read_only=on');raw.commit()
    if raw.execute('SHOW transaction_read_only').fetchone()[0]!='on':raise ValueError('Persistent write guard missing')
    raw.commit()
    with tempfile.TemporaryDirectory(prefix='legacy-synthetic-') as directory:
        root=Path(directory)
        settings=Settings(project_root=root,flex_token='',flex_query_id='',database_path=root/'unused.sqlite',
            database_url=uri,tenant_id=TENANT,tenant_name='Synthetic qualification',raw_report_dir=root/'raw',log_dir=root/'logs',
            timezone_name='Asia/Hong_Kong',daily_lookback_days=7,user_agent='offline-qualification',request_timeout_seconds=10,max_wait_seconds=20,
            azure_storage_account_url='')
        fixture=FIXTURE_CONTENT
        first=ingest_content(settings,fixture,run_type='qualification-fixture')
        if first['reconciliation']['status']!='PASS':raise ValueError('Synthetic first import failed')
        raw.commit();checks.append('synthetic_flex_import_reconciles')
        second=ingest_content(settings,fixture,run_type='qualification-fixture')
        if second['reconciliation']['status']!='PASS':raise ValueError('Synthetic replay reconciliation failed')
        if raw.execute('SELECT count(*) FROM trades').fetchone()[0]!=1 or raw.execute('SELECT count(*) FROM position_snapshots').fetchone()[0]!=1 or raw.execute('SELECT total_text FROM nav_snapshots').fetchone()[0]!='5004.00':raise ValueError('Duplicate or changed financial fixture rows')
        if raw.execute('SELECT count(*) FROM ingestion_runs').fetchone()[0]!=2:raise ValueError('Import history missing')
        raw.commit();checks.append('unchanged_replay_idempotent')
        # Exercise the original PostgreSQL-to-SQLite/CSV export against synthetic data.
        snapshot=root/'snapshot.sqlite';bundle=root/'exports.zip'
        manifest=export_snapshot(raw,snapshot,tenant_id=TENANT,generated_at='2026-10-10T02:00:00Z',
            exports=(('sample','SELECT account_id,snapshot_date,total_text FROM pg_temp.nav_snapshots ORDER BY account_id,snapshot_date'),))
        build_csv_zip(snapshot,bundle,manifest)
        import sqlite3,zipfile
        with sqlite3.connect(snapshot) as db:
            if db.execute('SELECT total_text FROM sample').fetchone()[0]!='5004.00':raise ValueError('Export value differs')
        with zipfile.ZipFile(bundle) as z:
            if '5004.00' not in z.read('sample.csv').decode():raise ValueError('CSV export differs')
        raw.commit();checks.append('sqlite_csv_export_preserves_values')
        rows=raw.execute('SELECT count(*) FROM trades').fetchone()[0];raw.commit()
        raw.execute("UPDATE pg_temp.trades SET quantity_text='999'");raw.rollback()
        if raw.execute("SELECT count(*) FROM pg_temp.trades WHERE quantity_text='999'").fetchone()[0] or raw.execute('SELECT count(*) FROM trades').fetchone()[0]!=rows:raise ValueError('Temporary rollback failed')
        raw.commit();checks.append('temporary_write_rollback')
    after=fingerprints();differences=[n for n in before if before[n]!=after[n]]
    if differences:
        print('LEGACY_PRESERVATION_DIAGNOSTIC '+json.dumps({'differences':{n:{'before':before[n],'after':after[n]} for n in differences},
            'timezone':raw.execute('SHOW TIME ZONE').fetchone()[0],
            'tenant':raw.execute("SELECT current_setting('app.tenant_id')").fetchone()[0],
            'read_only':raw.execute('SHOW transaction_read_only').fetchone()[0]}),flush=True)
        raise ValueError('Persistent recovery rows changed')
    print('LEGACY_INGESTION_PROOF '+json.dumps({'passed':True,'checks':checks,'public_tables_checked':25,
         'public_table_differences':differences,'temporary_relations':25,'database_read_only_enforced':True,
         'temporary_objects_dropped_on_disconnect':True,'production_connections':0,'production_writes':0,
         'persistent_clone_writes':0,'broker_calls':0,'cloud_storage_writes':0}),flush=True)
finally:raw.close()
