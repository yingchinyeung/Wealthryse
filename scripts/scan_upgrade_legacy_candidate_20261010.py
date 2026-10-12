"""Exact-image candidate scan in the original legacy registry."""
import json
import scan_ms_presentation_worker_candidate as scan
from release_upgrade_legacy_security_20261010 import REGISTRY,snapshot
scan.scan.REGISTRY=REGISTRY+'.azurecr.io'
scan.build.REGISTRY=REGISTRY


def unchanged(folder):
    if snapshot()!=json.loads((folder/'build-intent.json').read_bytes())['before_jobs']:
        raise ValueError('Legacy worker boundary drift')


scan.jobs_unchanged=unchanged
if __name__=='__main__':scan.main()
