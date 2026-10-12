"""Download only the accepted private QA outputs for local visual inspection."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261010-stage29/artifact-review-v1'


def main():
    if OUT.exists():raise ValueError('Preserve existing downloads')
    trial=protected(ROOT/'.work/platform-upgrade-20261010-stage29/fresh-report-v1/result.json')
    if trial.get('passed') is not True:raise ValueError('Passed trial required')
    artifacts=trial['proof']['artifacts']
    if len(artifacts)!=2 or {r['report_type'] for r in artifacts}!={'factsheet','presentation'}:raise ValueError('Two outputs required')
    OUT.mkdir()
    client=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    outputs=[]
    for record in artifacts:
        container,blob=record['output_reference'].split('/',1)
        if container!='upgrade-g04-reports-20261010' or not blob.startswith('review/alpha-world-t/2026-09-30/'):raise ValueError('QA-only outputs')
        source=client.get_blob_client(container,blob)
        if source.get_blob_properties().size>64*1024*1024:raise ValueError('Oversized report')
        content=source.download_blob().readall()
        if hashlib.sha256(content).hexdigest()!=record['sha256']:raise ValueError('QA output hash differs')
        name='AlphaWorld_T_QA_2026-09-30'+('.pdf' if record['report_type']=='factsheet' else '.pptx')
        (OUT/name).write_bytes(content)
        outputs.append({'report_type':record['report_type'],'file':str(OUT/name),'sha256':record['sha256']})
    client.close()
    save(OUT,'downloads',{'passed':True,'artifacts':outputs,'comparison':'Exact accepted QA output hashes','visual_acceptance':False,'live_report_files_changed':False})
    print('Two QA outputs downloaded and hash-verified; visual inspection pending')


if __name__=='__main__':main()
