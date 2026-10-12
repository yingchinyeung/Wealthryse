"""Copy and read back the exact MS cache rollback package into protected audit storage."""
import json,hashlib
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from release_platform_ms_risk_20261009 import BUNDLE,release
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

def main():
    result=release.read(BUNDLE/'cache-rehearsal-v1/result.json');proof=json.loads(result['records'][0]['Result'])
    if result['status']!='Succeeded' or not proof['passed'] or not proof['temporary_table_update_and_rollback_passed']:raise ValueError('Rehearsal not qualified')
    if not proof['private_blob'].startswith('review/platform-upgrade-20261009/ms-cache-rehearsal-'):raise ValueError('Unexpected archive path')
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    raw=service.get_blob_client('internal-reports',proof['private_blob']).download_blob().readall()
    if hashlib.sha256(raw).hexdigest()!=proof['blob_sha256']:raise ValueError('Backup bytes differ')
    package=json.loads(raw)
    if package['old_row']['payload_sha256']!=proof['old_payload_sha256'] or package['candidate_payload_sha256']!=proof['candidate_payload_sha256']:raise ValueError('Backup binding differs')
    receipt=retain(package,'platform-ms-risk-cache-recovery')
    restored,_=restore_protected(receipt)
    if restored!=package:raise ValueError('Protected rollback readback differs')
    release.save(BUNDLE/'cache-rehearsal-v1/protected.json',{'passed':True,'protected_receipt':receipt,
        'private_blob':proof['private_blob'],'blob_sha256':proof['blob_sha256'],'old_payload_sha256':proof['old_payload_sha256'],
        'candidate_payload_sha256':proof['candidate_payload_sha256'],'candidate_source_sha256':proof['candidate_source_sha256'],
        'temporary_table_update_and_rollback_passed':True})
    print('Cache rollback package protected and read back; live cache unchanged')

if __name__=='__main__':main()
