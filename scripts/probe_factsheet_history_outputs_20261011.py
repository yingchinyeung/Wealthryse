"""Installed-image generic PDF history verification against independent NAV math."""
import hashlib,json,os,re,socket,tempfile
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q


def sha(raw): return hashlib.sha256(raw).hexdigest()


def main():
    if os.getuid()!=10001 or q.TEST_DB!='upgrade_g04_migration_20261011' or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:
        raise ValueError('Exact private QA boundary required')
    for name,digest in SOURCE_HASHES.items():
        if sha((Path('/app')/name).read_bytes())!=digest: raise ValueError('Installed candidate source differs')
    from ibkr_etl.monthly_artifacts import render_factsheet
    from ibkr_etl.util import canonical_json
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient
    from pypdf import PdfReader
    prefix='candidate-stage34-factsheet-history-v1/'
    counts={};results=[];uploads=[];verified_rows=0
    with q.connect(q.TEST_DB) as db,ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID']) as identity,BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=identity) as service,tempfile.TemporaryDirectory() as folder:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on': raise ValueError('Read-only required')
        def state():
            return {t:sha('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM '+t+' t ORDER BY 1').fetchall()).encode()) for t in q.REPORT_TABLES}
        if state()!=EXPECTED_REPORT_HASHES: raise ValueError('Migrated QA state differs')
        bucket=service.get_container_client('upgrade-g04-reports-20261010')
        if bucket.get_container_properties().get('public_access') is not None: raise ValueError('Private QA container required')
        before={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
        if any(n.startswith(prefix) for n in before): raise ValueError('Preserve earlier output trial')
        rows=db.execute("SELECT subject_id,period_end,data_sha256,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND status IN ('frozen','approved') AND subject_id IN ('U16088571','U16088606','hong-kong-china') ORDER BY subject_id,period_end",(q.TENANT,)).fetchall()
        if len(rows)!=63: raise ValueError('Exactly 63 generic months required')
        root=Path(folder);templates={}
        for subject in ('U16088571','U16088606','hong-kong-china'):
            assets={}
            if subject=='U16088571':
                assets['template']={'container_name':'internal-reports','blob_name':'templates/U16088571/monthly-factsheet/approved-august-2026-v2.pdf','sha256':'460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d'}
            else:
                for r in db.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE subject_id=%s AND report_type='factsheet' AND asset_role IN ('template','completed_example') AND is_current AND approval_status IN ('validated','active') ORDER BY updated_at DESC",(subject,)).fetchall(): assets.setdefault(r['asset_role'],dict(r))
                if set(assets)!={'template','completed_example'}: raise ValueError('Approved template/model pair required')
            templates[subject]={}
            for role,asset in assets.items():
                if service.get_container_client(asset['container_name']).get_container_properties().get('public_access') is not None: raise ValueError('Private template required')
                raw=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
                if sha(raw)!=asset['sha256']: raise ValueError('Template hash differs')
                path=root/(subject+'-'+role+'.pdf');path.write_bytes(raw);templates[subject][role]=path
        for row in rows:
            subject=row['subject_id'];period=row['period_end'];raw=row['snapshot_payload_json']
            if sha(raw.encode())!=row['data_sha256']: raise ValueError('Frozen input hash differs')
            p=json.loads(raw)
            if p['subject_id']!=subject or p['period_end']!=period: raise ValueError('Frozen identity differs')
            series=p['performance']['series'];first=float(series[0]['nav']);months={}
            if first<=0 or [r['date'] for r in series]!=sorted(set(r['date'] for r in series)): raise ValueError('Invalid full NAV series')
            for r in series: months[r['date'][:7]]=r
            expected=[series[0]['date'],'100.00','-','-'];last=100.0
            for r in months.values():
                value=float(r['nav'])/first*100.0
                expected += [r['date'],f'{value:.2f}',f'{(value/last-1)*100:.2f}%',f'{(value/100-1)*100:.2f}%'];last=value
            path=root/(subject+'-'+period+'.pdf');assets=templates[subject]
            render_factsheet(p,template_path=assets['template'],design_model_path=assets.get('completed_example'),output_path=path)
            pdf=PdfReader(path)
            if len(pdf.pages)!=2: raise ValueError('Two-page factsheet required')
            visible=' '.join(pdf.pages[0].extract_text().split())
            if visible.count(' '.join(expected))!=1: raise ValueError('Rendered full-NAV table differs: '+subject+':'+period)
            if canonical_json(p)!=canonical_json(json.loads(raw)): raise ValueError('Renderer mutated frozen data')
            content=path.read_bytes();name=prefix+subject+'/'+period+'/factsheet.pdf'
            bucket.upload_blob(name,content,overwrite=False);uploads.append(name)
            if sha(bucket.download_blob(name).readall())!=sha(content): raise ValueError('Output readback differs')
            verified_rows+=1+len(months);counts[subject]=counts.get(subject,0)+1
            results.append({'subject':subject,'period_end':period,'input_sha256':row['data_sha256'],'output_reference':'upgrade-g04-reports-20261010/'+name,'sha256':sha(content),'history_rows_verified':1+len(months)})
            print('FACTSHEET_HISTORY_OUTPUT_MONTH '+json.dumps({'subject':subject,'period_end':period,'passed':True}),flush=True)
        if state()!=EXPECTED_REPORT_HASHES or counts!={'U16088571':21,'U16088606':21,'hong-kong-china':21}: raise ValueError('QA state/month coverage differs')
        receipt=canonical_json({'image':IMAGE,'factsheets':results,'visual_review':'UNVERIFIED'}).encode();name=prefix+'receipt.json'
        bucket.upload_blob(name,receipt,overwrite=False);uploads.append(name)
        if sha(bucket.download_blob(name).readall())!=sha(receipt): raise ValueError('Receipt integrity differs')
        after={b.name:(str(b.etag),b.size) for b in bucket.list_blobs()}
        if any(after.get(n)!=v for n,v in before.items()) or set(after)-set(before)!=set(uploads): raise ValueError('QA output scope differs')
    print('FACTSHEET_HISTORY_OUTPUT_FINAL '+json.dumps({'passed':True,'months':63,'factsheets':63,'history_rows_verified':verified_rows,'subjects':counts,'receipt_blob':name,'receipt_sha256':sha(receipt),'database_writes':0,'production_connection_used':False,'reports_registered':0,'approvals_changed':False,'full_visual_review':'UNVERIFIED'}),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('FACTSHEET_HISTORY_OUTPUT_ERROR '+json.dumps({'type':type(error).__name__,'message':str(error)[:350],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
