"""Exact-image scan with the same ARM representation as the frozen baseline."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
def unchanged(folder):
    expected=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    if set(expected)!={'ibkrv2stg-derived-daily'}:raise ValueError('Unexpected worker family')
    for name,binding in expected.items():
        live=request('GET',rid('Microsoft.App/jobs',name)+'?api-version=2024-03-01')
        if live['properties']['template']['containers'][0]['image']!=binding['image'] or settings_invariant(live)!=binding['settings_invariant_sha256']:raise ValueError('Derived baseline drift')
scan.jobs_unchanged=unchanged
if __name__=='__main__':scan.main()
