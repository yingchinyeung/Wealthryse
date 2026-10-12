"""Protect full historical cache preimages; never update live database."""
import json,hashlib
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from release_extended_crisis_identity_20261009 import STAGE as BUNDLE,release
from run_specialist_gateway_read_verification import retain,ACCOUNT,CONTAINER
def main():
    result=release.read(BUNDLE/'history-rehearsal-v2/result.json');proof=json.loads(result['records'][0]['Result'])
    if result['status']!='Succeeded' or not proof['passed'] or not proof['read_only'] or proof['database_writes']!=0 or proof['qualified']!=446:raise ValueError('Unexpected historical qualification')
    if not proof['private_blob'].startswith('review/platform-upgrade-20261009/ms-crisis-identity-history-'):raise ValueError('Unexpected blob')
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    raw=service.get_blob_client('internal-reports',proof['private_blob']).download_blob().readall()
    if hashlib.sha256(raw).hexdigest()!=proof['blob_sha256']:raise ValueError('Backup differs')
    package=json.loads(raw)
    if len(package['qualified'])!=446 or package['failures'] or {r['as_of'] for r in package['excluded']}!={'2025-01-20','2025-01-21'}:raise ValueError('Unexpected exclusions')
    for item in package['qualified']:
        if item['subject']!='alpha-world-ms' or not item['core_metrics_identical'] or item['old_row']['tenant_id']!='00000000-0000-0000-0000-000000000001':raise ValueError('Unexpected scope')
    receipt=retain(package,'platform-ms-crisis-identity-history-recovery')
    # The single-row helper has a 2 MiB limit. This explicitly bounded archive
    # contains 446 full preimages; do not weaken the general-purpose helper.
    expected=json.dumps(package,sort_keys=True,separators=(',',':')).encode()
    if len(expected)>128*1024**2:raise ValueError('Historical archive exceeds bound')
    archive=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',credential=AzureCliCredential())
    blob=archive.get_blob_client(CONTAINER,receipt['blob'],version_id=receipt['version_id'])
    props=blob.get_blob_properties()
    if props.version_id!=receipt['version_id'] or props.size!=len(expected):raise ValueError('Protected version/size differs')
    restored=blob.download_blob(offset=0,length=props.size+1).readall()
    if restored!=expected or hashlib.sha256(restored).hexdigest()!=receipt['sha256']:raise ValueError('Protected recovery readback differs')
    release.save(BUNDLE/'history-rehearsal-v2/protected.json',{'passed':True,'qualified':446,'excluded_insufficient_history':['2025-01-20','2025-01-21'],'protected_receipt':receipt,'private_blob':proof['private_blob'],'blob_sha256':proof['blob_sha256'],'database_writes':0})
    print('446 historical cache preimages and candidates protected; no historical caches modified')
if __name__=='__main__':main()
