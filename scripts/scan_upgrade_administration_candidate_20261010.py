"""Exact candidate scans with independently frozen per-job bindings."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant


def unchanged(folder):
    expected = json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']
    if len(expected) != 1 or not set(expected) <= {'ibkrv2stg-autonomous-admin', 'ibkrv2stg-autonomy-review'}:
        raise ValueError('Outside scoped administration family')
    for name, binding in expected.items():
        job = request('GET', rid('Microsoft.App/jobs', name) + '?api-version=2026-07-01')
        if job['properties']['template']['containers'][0]['image'] != binding['image'] or settings_invariant(job) != binding['settings_invariant_sha256']:
            raise ValueError('Installed job changed during scan')


scan.jobs_unchanged = unchanged
if __name__ == '__main__': scan.main()
