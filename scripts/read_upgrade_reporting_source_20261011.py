"""Read exact allowlisted installed source from the approved private QA capture."""
import hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/installed-reporting-source-v1'
def main():
    if OUT.exists():raise ValueError('Preserve earlier source capture')
    record=protected(ROOT/'.work/platform-upgrade-20261011-stage34/sector-integration-v4/result.json')
    proof=record['proof']
    if not record['passed'] or proof['source_blob']!='candidate-stage34-sector-v1/installed-application-source.json':raise ValueError('Exact passing capture required')
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        blob=service.get_blob_client('upgrade-g04-reports-20261010',proof['source_blob'])
        if blob.get_blob_properties().size>2*1024*1024:raise ValueError('Oversized source capture')
        raw=blob.download_blob().readall()
    if hashlib.sha256(raw).hexdigest()!=proof['source_sha256']:raise ValueError('Source capture changed')
    sources=json.loads(raw)
    if set(sources)!=set(proof['source_files_sha256']):raise ValueError('Inventory mismatch')
    for name,source in sources.items():
        if name.startswith('/') or '\\' in name or '..' in Path(name).parts or not name.startswith(('src/ibkr_etl/','scripts/')):raise ValueError('Invalid source path')
        if hashlib.sha256(source.encode()).hexdigest()!=proof['source_files_sha256'][name]:raise ValueError('Source file hash mismatch')
    OUT.mkdir()
    for name,source in sources.items():
        path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8',newline='\n') as f:f.write(source)
    save(OUT,'result',{'passed':True,'files':proof['source_files_sha256'],'source_trial':record['protected_receipt'],'private_source_only':True,'database_contents_downloaded':False})
    print(json.dumps({'source_files':len(sources),'verified':True}))
if __name__=='__main__':main()
