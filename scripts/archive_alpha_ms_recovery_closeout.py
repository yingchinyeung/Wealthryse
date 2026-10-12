"""Create-only archival under the existing reporting identity, no new RBAC."""
import base64
import hashlib
import json
import os
import zlib
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

data = zlib.decompress(base64.b64decode(CLOSEOUT_BYTES))
if hashlib.sha256(data).hexdigest() != CLOSEOUT_SHA256:
    raise ValueError('Recovery closeout fingerprint differs')
record = json.loads(data)
if record['status'] != 'original_source_recovery_and_private_corrected_factsheets_complete' or record['reports_released']:
    raise ValueError('Unexpected closeout scope')
blob = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
    credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client(
        'internal-reports','review/alpha-world-ms/source-recovery-20261007/closeout.json')
if blob.exists():
    if blob.download_blob().readall() != data:
        raise ValueError('Existing immutable closeout differs')
else:
    blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'),
        metadata={'sha256':CLOSEOUT_SHA256})
if hashlib.sha256(blob.download_blob().readall()).hexdigest() != CLOSEOUT_SHA256:
    raise ValueError('Closeout readback differs')
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'closeout_archived_and_readback_verified',
    'sha256':CLOSEOUT_SHA256,'database_writes':0,'rbac_changes':0}),flush=True)
