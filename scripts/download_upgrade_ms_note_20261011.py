"""Retrieve the two exact MS footnote QA artifacts without touching report releases."""
import hashlib
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage33/artifact-review-ms-note-v1'

def main():
    trial=protected(ROOT/'.work/platform-upgrade-20261011-stage33/ms-note-v1/result.json')
    proof=trial['proof']
    if trial.get('passed') is not True or proof.get('artifacts')!=2 or proof.get('registered_reports')!=0:
        raise ValueError('Two unregistered QA outputs required')
    if OUT.exists():raise ValueError('Preserve prior review files')
    if [r['subject'] for r in proof['subjects']]!=['alpha-world-ms']:raise ValueError('MS only')
    records=proof['subjects'][0]['artifacts']
    if len(records)!=2 or {r['report_type'] for r in records}!={'factsheet','presentation'}:raise ValueError('One pair only')
    save(OUT,'download-intent',{'scope':'Two MS-only QA outputs using existing read-only grant','trial':trial['protected_receipt']})
    outputs=[]
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as client:
        for r in records:
            container,name=r['output_reference'].split('/',1)
            suffix='factsheet.pdf' if r['report_type']=='factsheet' else 'presentation.pptx'
            if container!='upgrade-g04-reports-20261010' or name!='candidate-stage33-ms-note-v1/alpha-world-ms/'+suffix:raise ValueError('Non-QA output')
            blob=client.get_blob_client(container,name)
            if blob.get_blob_properties().size>64*1024*1024:raise ValueError('Oversized output')
            content=blob.download_blob().readall()
            if hashlib.sha256(content).hexdigest()!=r['sha256']:raise ValueError('Hash differs')
            path=OUT/('AlphaWorld_MS_Corrected_QA_note_2026-09-30'+('.pdf' if r['report_type']=='factsheet' else '.pptx'))
            with path.open('xb') as f:f.write(content)
            outputs.append({'subject':'alpha-world-ms','report_type':r['report_type'],'file':str(path),'sha256':r['sha256']})
    save(OUT,'downloads',{'passed':True,'artifacts':outputs,'exact_qa_hashes_verified':True,'visual_acceptance':'UNVERIFIED','production_files_changed':False})
    print('Two MS-only QA outputs downloaded with exact hashes')

if __name__=='__main__':main()
