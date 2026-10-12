"""Read-only independent exported numbers check for three generic QA decks."""
import hashlib,json,os,socket,sys,tempfile,types
from pathlib import Path
import qa_read_only as q

def main():
    sys.path[:0]=['/app/src','/app/scripts']
    for name,source in CANDIDATE_SOURCES.items():
        m=types.ModuleType(name);m.__package__='ibkr_etl' if name.startswith('ibkr_etl.') else '';sys.modules[name]=m;exec(source,m.__dict__)
    from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
    from ibkr_etl.util import canonical_json
    from audit_generic_report_numbers import audit
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Wrong recovery IP')
    results=[]
    with q.connect(q.TEST_DB) as db, BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])) as service, tempfile.TemporaryDirectory() as folder:
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('Read only')
                return db.execute(sql.replace('?', '%s'),args)
        for expected in EXPECTED_OUTPUTS:
            subject=expected['subject']
            row=db.execute('SELECT data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE subject_id=%s',(subject,)).fetchone()
            if row['data_sha256']!=expected['source_snapshot_sha256'] or hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256']:raise ValueError('Input drift')
            p=json.loads(row['snapshot_payload_json'])
            if subject not in ('U16088571','U16088606','hong-kong-china'):continue
            normalized=normalize_collected_report_holdings(Adapter(),subject,p['reporting_currency'],{'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
            for key in ('holdings','sector_allocation'):
                p[key]=normalized[key]['data'];p['source_citations'][key]+=normalized[key].get('citations',[])
            if hashlib.sha256(canonical_json(p).encode()).hexdigest()!=expected['candidate_payload_sha256']:raise ValueError('Candidate payload differs')
            artifact=next(r for r in expected['artifacts'] if r['report_type']=='presentation')
            container,name=artifact['output_reference'].split('/',1)
            if container!='upgrade-g04-reports-20261010' or name!='candidate-stage33-v3/'+subject+'/presentation.pptx':raise ValueError('Exact QA deck required')
            content=service.get_blob_client(container,name).download_blob().readall()
            if hashlib.sha256(content).hexdigest()!=artifact['sha256']:raise ValueError('Output hash differs')
            path=Path(folder)/(subject+'.pptx');path.write_bytes(content)
            results.append({'subject':subject,**audit(path,p)})
    print('UPGRADE_CORRECTED_NUMBERS '+json.dumps({'passed':True,'subjects':results,'database_writes':0,'uploads':0,'production_connection_used':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('UPGRADE_CORRECTED_NUMBERS_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:400]}),flush=True);raise SystemExit(1)
