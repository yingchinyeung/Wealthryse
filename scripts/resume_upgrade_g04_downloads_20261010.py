"""Resume only the denied QA download after the owner's exact scoped read grant."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from download_upgrade_g04_artifacts_20261010 import ROOT,OUT


def main():
    grant=protected(ROOT/'.work/platform-upgrade-20261010-stage29/qa-reader-v1/verified.json')
    if grant.get('passed') is not True or grant.get('production_scope_grants')!=0 or grant['role']!='Storage Blob Data Reader':raise ValueError('Approved scoped read grant required')
    trial=protected(ROOT/'.work/platform-upgrade-20261010-stage29/fresh-report-v1/result.json')
    records=trial['proof']['artifacts']
    names={'factsheet':'AlphaWorld_T_QA_2026-09-30.pdf','presentation':'AlphaWorld_T_QA_2026-09-30.pptx'}
    if trial.get('passed') is not True or len(records)!=2 or {r['report_type'] for r in records}!=set(names):raise ValueError('Exact QA outputs required')
    if not OUT.is_dir() or set(p.name for p in OUT.iterdir())-set(names.values())-{'download-intent.json','downloads.json'}:raise ValueError('Unexpected local artifacts; preserve them')
    if not (OUT/'download-intent.json').exists():save(OUT,'download-intent',{'scope':'Download only two accepted QA artifacts',
        'records':records,'grant':grant['protected_receipt'],'previous_attempt':'AuthorizationPermissionMismatch before any file was written; no access workaround used'})
    intent=protected(OUT/'download-intent.json')
    if intent['records']!=records:raise ValueError('Artifact identity drift')
    if (OUT/'downloads.json').exists():protected(OUT/'downloads.json');print('Existing verified downloads preserved');return
    client=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    accepted=[]
    for record in records:
        bucket,blob=record['output_reference'].split('/',1)
        if bucket!='upgrade-g04-reports-20261010' or not blob.startswith('review/alpha-world-t/2026-09-30/'):raise ValueError('QA-only output boundary')
        target=OUT/names[record['report_type']]
        if not target.exists():
            source=client.get_blob_client(bucket,blob)
            if source.get_blob_properties().size>64*1024*1024:raise ValueError('Oversized output')
            content=source.download_blob().readall()
            if hashlib.sha256(content).hexdigest()!=record['sha256']:raise ValueError('Remote hash differs')
            with target.open('xb') as handle:handle.write(content)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Preserve mismatched local output for investigation')
        accepted.append({'report_type':record['report_type'],'file':str(target),'sha256':record['sha256']})
    client.close()
    save(OUT,'downloads',{'passed':True,'artifacts':accepted,'comparison':'Exact accepted QA output hashes','visual_acceptance':False,'live_report_files_changed':False})
    print('Two private QA reports downloaded and verified')


if __name__=='__main__':main()
