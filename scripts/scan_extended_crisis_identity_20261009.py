"""Same scanner and gates; compare jobs using the build's ARM representation.

Azure CLI adds empty/default fields to its GET representation. Frozen build
intent was captured via ARM. Use that exact API and hash convention here;
never omit credentials, identity, schedule, commands or environment from checks.
"""
import json
import sys
from pathlib import Path
import scan_ms_presentation_worker_candidate as scanner
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

def exact_arm_jobs_unchanged(folder):
    expected=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    evidence=[]
    for name,binding in expected.items():
        if name not in ('ibkrv2stg-risk-snapshots','ibkrv2stg-benchmark-daily'):raise ValueError('Unexpected worker scope')
        job=request('GET',BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        if job['properties']['template']['containers'][0]['image']!=binding['image'] or settings_invariant(job)!=binding['settings_invariant_sha256']:
            raise ValueError('Exact ARM worker baseline changed: '+name)
        evidence.append({'job':name,'same_image':True,'same_settings_invariant':True})
    result={'passed':True,'method':'same ARM API and settings hash as frozen build intent','jobs':evidence}
    previous=folder/'scan-arm-preflight.json'
    if previous.exists():
        stored=json.loads(previous.read_bytes());remote,_=restore_protected(stored['protected_receipt'])
        if remote!=result or {k:v for k,v in stored.items() if k!='protected_receipt'}!=result:
            raise ValueError('Protected preflight changed')
        return
    with (folder/'scan-arm-preflight.json').open('x',encoding='utf-8') as stream:
        json.dump({**result,'protected_receipt':retain(result,'crisis-identity-scan-arm-preflight')},stream,indent=2)

if __name__=='__main__':
    scanner.jobs_unchanged=exact_arm_jobs_unchanged
    scanner.main()
