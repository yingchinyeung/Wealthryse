"""Diagnose the first failed QA subject with read-only SQL and uploads disabled."""
import ast,contextlib,hashlib,io,json,os,socket,sys,traceback
from pathlib import Path
from unittest.mock import patch
import qa_boundary as q

class ReachedUpload(RuntimeError):pass

def main():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    q.DATABASE='upgrade_g04_fresh_20261011';q.SUBJECT='U16088571'
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Recovery endpoint differs')
    source=Path('/app/scripts/generate_month_end_package.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Worker drift')
    safe_messages={n.args[0].value for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='RuntimeError' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)}
    os.environ['DATABASE_URL']=q.isolated_uri(os.environ['DATABASE_URL'])
    os.environ.update(PILOT_ACCOUNT_ID=q.SUBJECT,REPORT_PERIOD_END=q.PERIOD,INTERNAL_REPORT_CONTAINER=q.CONTAINER)
    for key in ('FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256','REPORT_DESIGN_VERSION','REPORT_BOUND_SNAPSHOT_ID','REPORT_BOUND_SNAPSHOT_SHA256','REPORTING_PERIOD_START'):os.environ.pop(key,None)
    original=psycopg.connect
    def guarded(conninfo='',**kwargs):
        opts=conninfo_to_dict(conninfo,**kwargs)
        if opts.get('host')!=q.HOST or opts.get('dbname')!=q.DATABASE or opts.get('hostaddr') or opts.get('service'):raise ValueError('Non-QA connection prohibited')
        kwargs.update(host=q.HOST,port=5432,dbname=q.DATABASE,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c app.tenant_id='+q.TENANT)
        return original(conninfo,**kwargs)
    psycopg.connect=guarded;sys.path[:0]=['/app/src','/app/scripts']
    import generate_month_end_package as worker
    from ibkr_etl.db import connect
    def inspect():
        db=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],q.TENANT)
        try:
            if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
            counts={t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('report_generation_runs','reporting_snapshots','report_artifact_releases','report_release_decisions')}
            return counts
        finally:db.close()
    before=inspect();stage='worker_preflight'
    render=worker.render_factsheet
    def traced_render(*a,**k):
        nonlocal stage
        stage='factsheet_render';result=render(*a,**k);stage='factsheet_identity_or_presentation';return result
    def no_upload(*a,**k):raise ReachedUpload('Private render reached upload boundary')
    caught=None
    try:
        with patch.object(worker,'render_factsheet',traced_render),patch.object(worker,'upload_bytes',no_upload),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):worker.main()
    except Exception as error:
        caught={'type':type(error).__name__,'stage':stage,'message':str(error) if str(error) in safe_messages else 'Message omitted; not an installed-source literal',
            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)[-6:]],
            'reached_upload_boundary':isinstance(error,ReachedUpload)}
    after=inspect()
    if before!=after or before['report_generation_runs']!=0:raise ValueError('Partial QA generation state exists')
    print('UPGRADE_REPORT_FAILURE_DIAG '+json.dumps({'passed':True,'read_only':True,'database_writes':0,'production_connection_used':False,'uploads_allowed':False,'qa_counts':after,'failure':caught}),flush=True)

if __name__=='__main__':main()
