"""Download hash-bound first/latest corrected PDFs for visual review only."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];STAGE=ROOT/'.work/platform-upgrade-20261011-stage34';OUT=STAGE/'factsheet-history-review-v1'
def sha(raw): return hashlib.sha256(raw).hexdigest()

def main():
    if OUT.exists(): raise ValueError('Preserve previous review downloads')
    trial=protected(STAGE/'factsheet-history-outputs-v1/result.json');proof=trial['proof']
    if not trial['passed'] or proof['factsheets']!=63 or proof['history_rows_verified']!=756: raise ValueError('Complete numerical QA required')
    container='upgrade-g04-reports-20261010';prefix='candidate-stage34-factsheet-history-v1/'
    if proof['receipt_blob']!=prefix+'receipt.json': raise ValueError('Exact receipt required')
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        raw=service.get_blob_client(container,proof['receipt_blob']).download_blob().readall()
        if sha(raw)!=proof['receipt_sha256']: raise ValueError('Receipt changed')
        receipt=json.loads(raw);rows=[r for r in receipt['factsheets'] if r['period_end'] in ('2025-01-31','2026-09-30')]
        if len(rows)!=6 or len({(r['subject'],r['period_end']) for r in rows})!=6: raise ValueError('Six exact first/latest PDFs required')
        save(OUT,'intent',{'source_trial':trial['protected_receipt'],'receipt_sha256':sha(raw),'scope':'Read-only local visual review of six hash-bound first/latest generic factsheets. No publication or database change.'})
        results=[]
        for row in rows:
            name=prefix+row['subject']+'/'+row['period_end']+'/factsheet.pdf'
            if row['output_reference']!=container+'/'+name: raise ValueError('Unexpected artifact path')
            client=service.get_blob_client(container,name)
            if client.get_blob_properties().size>16*1024*1024: raise ValueError('Bounded PDF required')
            data=client.download_blob().readall()
            if sha(data)!=row['sha256']: raise ValueError('PDF integrity differs')
            path=OUT/(row['subject']+'_'+row['period_end']+'.pdf')
            with path.open('xb') as stream: stream.write(data)
            results.append({**row,'file':str(path)})
        save(OUT,'downloads',{'passed':True,'artifacts':results,'full_visual_review':'UNVERIFIED','deployed':False})
        print(json.dumps({'downloaded':6,'verified_hashes':True,'deployed':False}))
if __name__=='__main__': main()
