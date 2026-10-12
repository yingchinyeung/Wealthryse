"""Inspect all persisted QA pairs after a shape-check failure; never regenerate."""
import hashlib,io,json,os,re,socket,sys,zipfile
from pathlib import Path
import qa_boundary as q

def main():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    q.DATABASE='upgrade_g04_fresh_20261011'
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong recovery IP')
    os.environ['DATABASE_URL']=q.isolated_uri(os.environ['DATABASE_URL']);original=psycopg.connect
    def guarded(conninfo='',**kwargs):
        opts=conninfo_to_dict(conninfo,**kwargs)
        if opts.get('host')!=q.HOST or opts.get('dbname')!=q.DATABASE or opts.get('hostaddr') or opts.get('service'):raise ValueError('Non-QA connection prohibited')
        kwargs.update(host=q.HOST,port=5432,dbname=q.DATABASE,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c app.tenant_id='+q.TENANT)
        return original(conninfo,**kwargs)
    psycopg.connect=guarded;sys.path[:0]=['/app/src','/app/scripts']
    from ibkr_etl.db import connect
    from ibkr_etl.cloud_storage import download_blob_bytes
    from pypdf import PdfReader
    db=connect(Path('/tmp/unused.sqlite3'),os.environ['DATABASE_URL'],q.TENANT);subjects=[]
    try:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
        counts={t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('report_generation_runs','reporting_snapshots','report_artifact_releases','report_release_decisions')}
        if counts!={'report_generation_runs':10,'reporting_snapshots':5,'report_artifact_releases':0,'report_release_decisions':0}:raise ValueError('Unexpected partial registration')
        storage=os.environ['AZURE_STORAGE_ACCOUNT_URL']
        if storage!='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net':raise ValueError('Unexpected storage')
        for subject in ('U16088571','U16088606','alpha-world-ms','alpha-world-t','hong-kong-china'):
            q.SUBJECT=subject;rows=q.metadata(db);artifacts=[]
            if len(rows)!=2 or {r['report_type'] for r in rows}!={'factsheet','presentation'}:raise ValueError('Exactly one QA pair required')
            for r in rows:
                container,name=r['output_reference'].split('/',1)
                if r['status']!='awaiting_approval' or container!=q.CONTAINER or not name.startswith('review/'+subject+'/2026-09-30/'):raise ValueError('Non-QA or released output')
                content=download_blob_bytes(storage,container,name,maximum_bytes=64*1024*1024)
                if hashlib.sha256(content).hexdigest()!=r['output_sha256']:raise ValueError('Artifact hash differs')
                if r['report_type']=='factsheet':
                    doc=PdfReader(io.BytesIO(content));count=len(doc.pages)
                    sizes=[{'width':float(p.mediabox.width),'height':float(p.mediabox.height)} for p in doc.pages]
                else:
                    with zipfile.ZipFile(io.BytesIO(content)) as z:count=sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml',n)) for n in z.namelist())
                    sizes=[]
                artifacts.append({'report_type':r['report_type'],'generation_run_id':r['generation_run_id'],'output_reference':r['output_reference'],'sha256':r['output_sha256'],'pages_or_slides':count,'page_dimensions':sizes})
            designs=[]
            for row in db.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE subject_id=? AND report_type='factsheet' AND is_current AND approval_status IN ('validated','active') ORDER BY updated_at DESC",(subject,)).fetchall():
                if row['asset_role'] not in ('template','completed_example'):continue
                data=download_blob_bytes(storage,row['container_name'],row['blob_name'],maximum_bytes=16*1024*1024)
                if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Approved design hash differs')
                designs.append({'role':row['asset_role'],'pages':len(PdfReader(io.BytesIO(data)).pages),'sha256':row['sha256']})
            subjects.append({'subject':subject,'snapshot_id':str(rows[0]['snapshot_id']),'artifacts':artifacts,'approved_design_page_counts':designs})
    finally:db.close()
    print('UPGRADE_REPORT_READBACK '+json.dumps({'passed':True,'subjects':subjects,'database_writes':0,'uploads_allowed':False,'production_connection_used':False,'qa_counts':counts,'all_ten_hashes_verified':True,'visual_acceptance':'UNVERIFIED','g04_complete':False}),flush=True)

if __name__=='__main__':main()
