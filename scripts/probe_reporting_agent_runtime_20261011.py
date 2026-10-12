"""Actual installed sector function against private, frozen September inputs."""
import hashlib,json,os,socket
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q

def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}: raise ValueError('Private QA boundary required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest: raise ValueError('Installed source drift')
    from ibkr_etl.agent_tools import _sector_allocation
    with q.connect(q.TEST_DB) as db:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on': raise ValueError('Read-only required')
        class Adapter:
            def execute(self,sql,args=()):
                if not sql.lstrip().upper().startswith('SELECT '): raise ValueError('SELECT only')
                return db.execute(sql.replace('?','%s'),args)
        def state():
            return hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
        if state()!=EXPECTED: raise ValueError('Qualified private snapshot state differs')
        rows=db.execute("SELECT subject_id,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND period_end='2026-09-30' AND status IN ('frozen','approved') ORDER BY subject_id",(q.TENANT,)).fetchall()
        if len(rows)!=5: raise ValueError('Five subjects required')
        results=[]
        for row in rows:
            raw=row['snapshot_payload_json'];p=json.loads(raw);subject=row['subject_id']
            if hashlib.sha256(raw.encode()).hexdigest()!=row['data_sha256'] or p['subject_id']!=subject: raise ValueError('Input hash/identity differs')
            actual=_sector_allocation(Adapter(),subject,p['data_as_of'],{}).as_dict()['data']
            if subject in ('alpha-world-ms','alpha-world-t'):
                if actual['classification_basis']!='composite sleeve type' or actual!=p['sector_allocation']: raise ValueError('Composite sector path changed')
            else:
                for key in ('currency','valuation_method','gross_market_value','classified_market_value','allocation','allocation_detail','classifications'):
                    if actual[key]!=p['sector_allocation'][key]: raise ValueError('Installed sector mismatch: '+subject+':'+key)
            results.append({'subject':subject,'passed':True,'input_sha256':row['data_sha256']})
        if state()!=EXPECTED: raise ValueError('QA state changed')
    print('REPORTING_AGENT_RUNTIME_FINAL '+json.dumps({'passed':True,'consumer':CONSUMER,'image':IMAGE,'subjects':results,'source_hashes':SOURCE_HASHES,'database_writes':0,'production_connection_used':False,'source_overlays':False}),flush=True)

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('REPORTING_AGENT_RUNTIME_ERROR '+json.dumps({'type':type(error).__name__,'message':str(error)[:350],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
