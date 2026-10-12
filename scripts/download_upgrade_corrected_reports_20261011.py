"""Download hash-bound private QA outputs using the existing QA-only read grant."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-v1'
NAMES={'alpha-world-ms':'AlphaWorld_MS','alpha-world-t':'AlphaWorld_T','hong-kong-china':'HongKong_China','U16088571':'U16088571','U16088606':'U16088606'}

def main():
    trial=protected(ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v3/result.json')
    if trial.get('passed') is not True or trial['proof'].get('artifacts')!=10 or trial['proof'].get('registered_reports')!=0:raise ValueError('Ten intact QA outputs required')
    if OUT.exists():raise ValueError('Preserve prior review files')
    records=trial['proof']['subjects']
    if {r['subject'] for r in records}!=set(NAMES):raise ValueError('Unexpected subjects')
    save(OUT,'download-intent',{'scope':'Ten accepted QA outputs only; existing owner QA-container Reader; no grants or production writes','trial':trial['protected_receipt']})
    client=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    outputs=[]
    try:
        for item in records:
            subject=item['subject']
            if {r['report_type'] for r in item['artifacts']}!={'factsheet','presentation'} or len(item['artifacts'])!=2:raise ValueError('Exactly one pair required')
            for r in item['artifacts']:
                container,name=r['output_reference'].split('/',1)
                if container!='upgrade-g04-reports-20261010' or not name.startswith('candidate-stage33-v3/'+subject+'/') or any(p in ('','.','..') for p in name.split('/')):raise ValueError('Non-QA output')
                blob=client.get_blob_client(container,name)
                if blob.get_blob_properties().size>64*1024*1024:raise ValueError('Oversized report')
                content=blob.download_blob().readall()
                if hashlib.sha256(content).hexdigest()!=r['sha256']:raise ValueError('Downloaded hash differs')
                path=OUT/(NAMES[subject]+'_Corrected_QA_2026-09-30'+('.pdf' if r['report_type']=='factsheet' else '.pptx'))
                with path.open('xb') as f:f.write(content)
                outputs.append({'subject':subject,'report_type':r['report_type'],'file':str(path),'sha256':r['sha256']})
    finally:client.close()
    save(OUT,'downloads',{'passed':True,'artifacts':outputs,'exact_qa_hashes_verified':True,'visual_acceptance':'UNVERIFIED','production_files_changed':False})
    print('Ten exact QA outputs downloaded; visual inspection pending')

if __name__=='__main__':main()
