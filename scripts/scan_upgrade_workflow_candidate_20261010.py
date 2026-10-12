"""Exact two-workflow candidate scans with unchanged live-binding checks."""
import json
import scan_ms_presentation_worker_candidate as scan
from release_upgrade_workflow_security_20261010 import TARGETS,snapshot


def unchanged(folder):
    target=folder.name.split('-security-v1')[0]
    if target not in TARGETS:raise ValueError('Outside workflow scope')
    wanted=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']
    if snapshot(target)!=wanted:raise ValueError('Original workflow changed')


scan.jobs_unchanged=unchanged
if __name__=='__main__':scan.main()
