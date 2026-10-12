"""Inventory immutable QA history and compute reporting-only correction hashes."""
import collections,hashlib,json,os,socket,sys
from pathlib import Path
import qa_read_only as q
def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_20261010':raise ValueError('Exact QA boundary required')
    if {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private recovery endpoint required')
    for n,h in SOURCE_HASHES.items():
        if sha((Path('/app')/n).read_bytes())!=h:raise ValueError('Installed candidate drift')
    sys.path[:0]=['/app/src','/app/scripts']
    from ibkr_etl.reporting_currency_holdings import normalize_collected_report_holdings
    from ibkr_etl.util import canonical_json
    with q.connect(q.TEST_DB) as db:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Readonly required')
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
                return db.execute(sql.replace('?', '%s'),args)
        def fingerprint():return {t:sha('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()) for t in q.REPORT_TABLES}
        before=fingerprint();subjects=('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t')
        rows=db.execute("SELECT snapshot_id,subject_id,period_end,status,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND subject_id=ANY(%s) AND status IN ('frozen','approved') ORDER BY subject_id,period_end,snapshot_id",(q.TENANT,list(subjects))).fetchall()
        groups=collections.Counter((r['subject_id'],str(r['period_end'])) for r in rows);results=[]
        for row in rows:
            sid=row['subject_id'];period=str(row['period_end']);item={'subject':sid,'period_end':period,'snapshot_id':str(row['snapshot_id']),'status':row['status'],'source_sha256':row['data_sha256']}
            if groups[(sid,period)]!=1:item['blocker']='multiple_active_snapshots';results.append(item);continue
            raw=row['snapshot_payload_json'];raw=raw if isinstance(raw,str) else canonical_json(raw)
            if sha(raw.encode())!=row['data_sha256']:raise ValueError('Stored historical hash differs')
            p=json.loads(raw);original=json.loads(raw)
            try:
                if sid in subjects[:3]:
                    if any(p.get(k,{}).get('valuation_method') for k in ('holdings','sector_allocation')):raise ValueError('Already marked input needs explicit idempotent review')
                    values=normalize_collected_report_holdings(Adapter(),sid,p['reporting_currency'],{'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
                    for k in ('holdings','sector_allocation'):
                        p[k]=values[k]['data'];p['source_citations'][k]=[*p['source_citations'][k],*values[k].get('citations',[])]
                if any(p[k]!=original[k] for k in original if k not in ('holdings','sector_allocation','source_citations')):raise ValueError('Non-reporting values changed')
                item.update(candidate_sha256=sha(canonical_json(p).encode()),nav_returns_risk_unchanged=True,holdings_count=len(p['holdings'].get('holdings',[])),needs_snapshot_correction=p!=original)
            except ValueError as error:item['blocker']=str(error)[:250]
            package=db.execute("SELECT DISTINCT ON (report_type) report_type,status,template_version,output_sha256 FROM report_generation_runs WHERE tenant_id=%s AND snapshot_id=%s AND report_type IN ('factsheet','presentation') ORDER BY report_type,generation_run_id DESC",(q.TENANT,row['snapshot_id'])).fetchall()
            item['existing_packages']=[dict(r) for r in package];results.append(item)
        if fingerprint()!=before:raise ValueError('QA history changed')
    summary={sid:{'snapshots':sum(r['subject']==sid for r in results),'corrections':sum(r['subject']==sid and r.get('needs_snapshot_correction',False) for r in results),'blockers':sum(r['subject']==sid and bool(r.get('blocker')) for r in results)} for sid in subjects}
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    content=canonical_json({'rows':results,'summary':summary,'qa_fingerprints':before,'no_records_changed':True}).encode()
    name='candidate-stage34-historical-plan-v1/inventory.json'
    with ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as identity,BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=identity) as service:
        bucket=service.get_container_client('upgrade-g04-reports-20261010')
        if bucket.get_container_properties().get('public_access') is not None:raise ValueError('Private QA only')
        bucket.upload_blob(name,content,overwrite=False)
        if sha(bucket.download_blob(name).readall())!=sha(content):raise ValueError('Readback differs')
    print('REPORTING_HISTORY_FINAL '+json.dumps({'passed':True,'summary':summary,'inventory_blob':name,'inventory_sha256':sha(content),'qa_report_state_unchanged':True,'database_writes':0,'production_connection_used':False,'migration_executed':False}),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_HISTORY_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:500],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
