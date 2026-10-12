"""Exact immutable Debian/Python runtime scan; retain and assess every finding."""
import json
import scan_ms_presentation_worker_candidate as scan
from release_upgrade_owner_runtime_20261010 import snapshot


def unchanged(folder):
    if snapshot() != json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']:
        raise ValueError('Owner runtime definition changed')


original_assess = scan.assess_container_scan


def assess(**kwargs):
    report = json.loads(kwargs['report_bytes'])
    observed = {r.get('Type') for r in report.get('Results', []) if r.get('Type')}
    if observed != {'debian', 'python-pkg'}: raise ValueError('Unexpected runtime package ecosystem; review complete coverage')
    return original_assess(**kwargs, required_types=frozenset(('debian', 'python-pkg')))


scan.jobs_unchanged = unchanged
scan.assess_container_scan = assess
if __name__ == '__main__': scan.main()
