"""Verify the two already-approved conflicting files in memory, retain no copies."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-blobs-v1'

def main():
    if OUT.exists():raise ValueError('Preserve prior verification')
    proof=protected(OUT.parent/'duplicates-v1/result.json')['proof']
    rows=proof['duplicate_registrations']
    if len(rows)!=2 or [r['artifact_release_id'] for r in rows]!=[55,57] or any((r['subject_id'],r['period_end'],r['report_type'],r['snapshot_id'],r['release_decision_id'])!=('alpha-world-ms','2026-09-30','factsheet','7a8d6b37-f9cf-555d-9927-74b0389cae4e',28) for r in rows):raise ValueError('Unexpected duplicate set')
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    checks=[]
    for row in rows:
        if row['container_name']!='internal-reports' or not row['blob_name'].startswith('released/alpha-world-ms/2026-09-30/'):raise ValueError('Unexpected report path')
        client=service.get_blob_client(row['container_name'],row['blob_name']);before=client.get_blob_properties()
        if not 0<before.size<=10*1024*1024:raise ValueError('Unexpected file size')
        content=client.download_blob().readall();digest=hashlib.sha256(content).hexdigest()
        after=client.get_blob_properties()
        if before.etag!=after.etag or len(content)!=before.size or digest!=row['artifact_sha256'] or not content.startswith(b'%PDF-'):raise ValueError('Registered report integrity failed')
        checks.append({'artifact_release_id':row['artifact_release_id'],'sha256':digest,'bytes':len(content),'etag':before.etag,'version_id':before.version_id,'passed':True})
        del content
    save(OUT,'result',{'passed':True,'checks':checks,'local_report_files_created':0,'reports_modified':False,'approvals_modified':False,'visual_acceptance_claimed':False})
    print(json.dumps({'passed':True,'files':2,'local_report_files_created':0}))

if __name__=='__main__':main()
