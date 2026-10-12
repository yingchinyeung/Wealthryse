"""Scan exact qualified broker candidate and preserve its unchanged live boundary."""
import json
import scan_ms_presentation_worker_candidate as scan
from deploy_viewer_private_admin_20261008 import request
from deploy_platform_access_correction_20261009 import invariant


def unchanged(folder):
    expected = json.loads((folder / 'build-intent.json').read_bytes())['before_application']
    if not expected['id'].endswith('/ibkrv2stg-credential-broker'):
        raise ValueError('Exact broker target required')
    live = request('GET', expected['id'] + '?api-version=2024-03-01')
    if live['properties']['template']['containers'][0]['image'] != expected['image'] or invariant(live) != expected['invariant']:
        raise ValueError('Installed broker image or settings changed')


scan.jobs_unchanged = unchanged
if __name__ == '__main__':
    scan.main()
