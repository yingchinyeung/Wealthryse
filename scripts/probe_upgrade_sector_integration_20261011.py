"""Read-only integration proof and exact installed-source capture, no DB export."""
import hashlib,json,os,socket,sys,types
from pathlib import Path
import qa_read_only as q

def main():
    if os.getuid()!=10001:raise ValueError('Non-root required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Recovery address differs')
    sys.path[:0]=['/app/src','/app/scripts']
    installed={n:(Path('/app')/n).read_text(encoding='utf-8') for n in SOURCE_FILES}
    for name,source in CANDIDATES.items():
        m=types.ModuleType(name);m.__package__='ibkr_etl';sys.modules[name]=m;exec(source,m.__dict__)
    from ibkr_etl.agent_tools import _sector_allocation
    from ibkr_etl.reporting_snapshot import _collect_tool_results
    from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
    with q.connect(q.TEST_DB) as db:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Readonly required')
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(sql.replace('?', '%s'),args)
        adapter=Adapter()
        def fingerprints():
            return {t:hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()).hexdigest() for t in q.REPORT_TABLES}
        before=fingerprints()
        rows=db.execute('SELECT subject_id,data_sha256,snapshot_payload_json FROM reporting_snapshots ORDER BY subject_id').fetchall()
        if {r['subject_id']:r['data_sha256'] for r in rows}!=EXPECTED_HASHES:raise ValueError('Frozen QA drift')
        results=[]
        for row in rows:
            p=json.loads(row['snapshot_payload_json']);subject=row['subject_id']
            if hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256']:raise ValueError('Input hash differs')
            if subject in ('alpha-world-ms','alpha-world-t'):
                result=_sector_allocation(adapter,subject,p['data_as_of'],{}).as_dict()['data']
                if result['classification_basis']!='composite sleeve type':raise ValueError('Composite path changed')
                results.append({'subject':subject,'composite_path_unchanged':True});continue
            expected=normalize_collected_report_holdings(adapter,subject,p['reporting_currency'],{
                'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
            sector=_sector_allocation(adapter,subject,p['data_as_of'],{}).as_dict()
            collected=_collect_tool_results(adapter,tenant_id=q.TENANT,subject_id=subject,period_end=p['period_end'],reporting_currency=p['reporting_currency'])
            for actual in (sector,collected['sector_allocation']):
                for key in ('currency','valuation_method','gross_market_value','classified_market_value','allocation','allocation_detail','classifications'):
                    if actual['data'][key]!=expected['sector_allocation']['data'][key]:raise ValueError('Sector integration mismatch: '+subject+':'+key)
            if collected['holdings']['data']!=expected['holdings']['data']:raise ValueError('Collector holdings mismatch: '+subject)
            results.append({'subject':subject,'standalone_and_collector_agree':True,'currency':p['reporting_currency'],'gross_market_value':sector['data']['gross_market_value'],'holdings':len(collected['holdings']['data']['holdings'])})
        if fingerprints()!=before:raise ValueError('QA state changed')
    # Application-source only. No database records, credentials or snapshot payloads.
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    content=json.dumps(installed,sort_keys=True).encode();digest=hashlib.sha256(content).hexdigest()
    with ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as identity, BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=identity) as service:
        bucket=service.get_container_client('upgrade-g04-reports-20261010')
        if bucket.get_container_properties().get('public_access') is not None:raise ValueError('Private source storage required')
        name='candidate-stage34-sector-v1/installed-application-source.json'
        bucket.upload_blob(name,content,overwrite=False)
        if hashlib.sha256(bucket.download_blob(name).readall()).hexdigest()!=digest:raise ValueError('Source readback mismatch')
    print('UPGRADE_SECTOR_FINAL '+json.dumps({'passed':True,'subjects':results,'source_blob':name,'source_sha256':digest,'source_files_sha256':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in installed.items()},'qa_state_unchanged':True,'database_writes':0,'production_connection_used':False,'saved_job_changed':False,'deployed':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('UPGRADE_SECTOR_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:500],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
