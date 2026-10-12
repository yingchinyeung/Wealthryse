"""Scan full Debian/Python/Node inventory of the immutable sync candidate."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant

def unchanged(folder):
    expected = json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']
    if set(expected) != {'ibkrv2stg-account-sync-daily'}: raise ValueError('Wrong target family')
    for name, item in expected.items():
        job = request('GET', rid('Microsoft.App/jobs', name) + '?api-version=2024-03-01')
        if job['properties']['template']['containers'][0]['image'] != item['image'] or settings_invariant(job) != item['settings_invariant_sha256']: raise ValueError('Installed sync configuration changed')

scan.jobs_unchanged = unchanged
if __name__ == '__main__': scan.main()
