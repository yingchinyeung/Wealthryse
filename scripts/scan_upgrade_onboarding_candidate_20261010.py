"""Qualified candidate scan; no runtime changes."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant


def unchanged(folder):
    jobs=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    if set(jobs)!={'ibkrv2stg-onboard-analytics'}: raise ValueError('Outside onboarding scope')
    for name,wanted in jobs.items():
        live=request('GET',rid('Microsoft.App/jobs',name)+'?api-version=2026-07-01')
        if live['properties']['template']['containers'][0]['image']!=wanted['image'] or settings_invariant(live)!=wanted['settings_invariant_sha256']:
            raise ValueError('Installed onboarding settings drift')


scan.jobs_unchanged=unchanged
if __name__=='__main__':scan.main()
