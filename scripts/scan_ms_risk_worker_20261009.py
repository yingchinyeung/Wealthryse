"""Use the same ARM representation used to freeze this worker's baseline."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_metadata_monitor import request,BASE
from build_ms_presentation_worker_candidate import invariant

def jobs_unchanged(folder):
    expected=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    if set(expected)!={'ibkrv2stg-risk-snapshots'}:raise ValueError('Unexpected worker scope')
    for name,binding in expected.items():
        job=request('GET',BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        if (job['properties']['template']['containers'][0]['image']!=binding['image']
                or invariant(job)!=binding['settings_invariant_sha256']):raise ValueError('Exact ARM baseline differs')

scan.jobs_unchanged=jobs_unchanged
if __name__=='__main__':scan.main()
