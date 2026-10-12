"""Scan exact candidate while checking the three frozen worker boundaries."""
import json
import scan_ms_presentation_worker_candidate as scan
from release_upgrade_institutional_security_20261010 import snapshot


def unchanged(folder):
    expected = json.loads((folder / 'build-intent.json').read_bytes())['before_jobs']
    if snapshot() != expected:
        raise ValueError('Live institutional worker configuration changed')


scan.jobs_unchanged = unchanged
if __name__ == '__main__': scan.main()
