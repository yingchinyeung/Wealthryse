"""Download only hash-verified January private outputs for native visual review."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/january-native-review-v1'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    if OUT.exists():raise ValueError('Preserve prior download')
    trial=protected(OUT.parent/'historical-outputs-U16088571-v2/result.json');proof=trial['proof']
    if not trial['passed'] or proof['months']!=21:raise ValueError('Accepted historical batch required')
    prefix='candidate-stage34-historical-v2/U16088571/';container='upgrade-g04-reports-20261010'
    if proof['receipt_blob']!=prefix+'receipt.json':raise ValueError('Exact receipt path required')
    outputs=[]
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        raw=service.get_blob_client(container,proof['receipt_blob']).download_blob().readall()
        if sha(raw)!=proof['receipt_sha256']:raise ValueError('Receipt integrity differs')
        receipt=json.loads(raw);months=[r for r in receipt['months'] if r['period_end']=='2025-01-31']
        if len(months)!=1 or len(months[0]['artifacts'])!=2:raise ValueError('Exact January pair required')
        save(OUT,'intent',{'source_trial':trial['protected_receipt'],'receipt_sha256':sha(raw),'scope':'Read-only download for native local review; no release or approval'})
        for item in months[0]['artifacts']:
            ext='pdf' if item['report_type']=='factsheet' else 'pptx';name=prefix+'2025-01-31/'+('factsheet.pdf' if ext=='pdf' else 'presentation.pptx')
            if item['output_reference']!=container+'/'+name:raise ValueError('Outside January scope')
            blob=service.get_blob_client(container,name)
            if blob.get_blob_properties().size>32*1024*1024:raise ValueError('Output too large')
            data=blob.download_blob().readall()
            if sha(data)!=item['sha256']:raise ValueError('Artifact integrity differs')
            path=OUT/('U16088571_2025-01-31_Corrected.'+ext)
            with path.open('xb') as f:f.write(data)
            outputs.append({'file':str(path),'sha256':sha(data),'report_type':item['report_type']})
    save(OUT,'downloads',{'passed':True,'artifacts':outputs,'native_visual_review':'UNVERIFIED','deployed':False})
    print(json.dumps({'downloaded':outputs}))
if __name__=='__main__':main()
