"""Read only the private historical correction inventory, not broker records."""
import collections,hashlib,json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/historical-inventory-v1'
TRIAL=OUT.parent/'historical-plan-v1/result.json'
BLOB='candidate-stage34-historical-plan-v1/inventory.json'
def main():
    if OUT.exists():raise ValueError('Preserve previous inventory')
    trial=protected(TRIAL);proof=trial['proof']
    if not trial['passed'] or proof['inventory_blob']!=BLOB:raise ValueError('Exact passing inventory required')
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        blob=service.get_blob_client('upgrade-g04-reports-20261010',proof['inventory_blob'])
        if blob.get_blob_properties().size>512*1024:raise ValueError('Oversized inventory')
        raw=blob.download_blob().readall()
    if hashlib.sha256(raw).hexdigest()!=proof['inventory_sha256']:raise ValueError('Inventory changed')
    value=json.loads(raw)
    if value['summary']!=proof['summary'] or not value['no_records_changed']:raise ValueError('Inventory proof differs')
    OUT.mkdir()
    with (OUT/'inventory.json').open('xb') as f:f.write(raw)
    blockers=dict(collections.Counter(r['blocker'] for r in value['rows'] if r.get('blocker')))
    states=dict(collections.Counter(p['status'] for r in value['rows'] for p in r.get('existing_packages',[])))
    save(OUT,'result',{'passed':True,'trial':trial['protected_receipt'],'inventory_sha256':proof['inventory_sha256'],'summary':value['summary'],'blockers':blockers,'existing_package_states':states,'deployed':False,'migration_executed':False})
    print(json.dumps({'summary':value['summary'],'blockers':blockers,'existing_package_states':states}))
if __name__=='__main__':main()
