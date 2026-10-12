"""Use the same job-invariant definition as this candidate's build boundary."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant


def unchanged(folder):
    jobs = json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']
    if set(jobs) != {'ibkrv2stg-metadata-monitor'}:
        raise ValueError('Exact metadata job scope required')
    for name, expected in jobs.items():
        live = request('GET', rid('Microsoft.App/jobs', name) + '?api-version=2024-03-01')
        if live['properties']['template']['containers'][0]['image'] != expected['image'] or settings_invariant(live) != expected['settings_invariant_sha256']:
            raise ValueError('Live metadata image/settings changed')


scan.jobs_unchanged = unchanged
if __name__ == '__main__': scan.main()
