"""Exact-image scan using the same ARM API as the frozen worker baseline.

Avoid comparing different CLI/REST schemas. Every identity, secret reference,
schedule and non-image template value remains bound by the original hash.
"""
import json
from pathlib import Path
import scan_ms_presentation_worker_candidate as scan
from deploy_metadata_monitor import request,BASE

def jobs_unchanged(folder):
    expected=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    if set(expected)!={'ibkrv2stg-month-end-factsheet'}:
        raise ValueError('Unexpected worker scope')
    for name,binding in expected.items():
        live=request('GET',BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
        if (live['properties']['template']['containers'][0]['image']!=binding['image']
            or scan.build.invariant(live)!=binding['settings_invariant_sha256']):
            raise ValueError('Exact ARM worker baseline changed')

scan.jobs_unchanged=jobs_unchanged
if __name__=='__main__':scan.main()
