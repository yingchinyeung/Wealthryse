"""Apply the snapshot image's established Debian/Python policy, never waive findings."""
import json
from pathlib import Path
import release_snapshot_correction_20261011 as release
from rehearse_ms_worker_image_rollback import restore_protected

def main():
    root=release.ROOT
    previous=root/'.work/platform-upgrade-20261010-stage23/snapshot-security-v1'
    saved=json.loads((previous/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((previous/'scan-protected-receipt.json').read_bytes()))
    intent=release.h.read(release.OUT/'source-intent.json')
    if saved!=remote or saved['image']!=intent['base_image'] or saved['assessment']['covered_types']!=['debian','python-pkg'] or saved['assessment']['status']!='pass':
        raise ValueError('Previously reviewed Python-only base policy required')
    if any(not (n.endswith('.py') or n==release.ASSET) for n in intent['candidate_hashes']):
        raise ValueError('Only Python code and the hash-bound market asset may be added')
    source=(root/'scripts/reconcile_upgrade_legacy_scan_20261010.py').read_text()
    changes={
        'from release_upgrade_legacy_security_20261010 import OUT, r':'from release_snapshot_correction_20261011 import OUT, h as r',
        'from scan_upgrade_legacy_candidate_20261010 import unchanged':'from release_snapshot_correction_20261011 import unchanged as live_unchanged\nunchanged = lambda folder: live_unchanged()',
        'The legacy runtime contains Debian and Python, not Node.':'The exact snapshot base has a protected Debian/Python build policy. Only Python modules and one existing hash-bound public-market asset are added; no Node runtime is introduced.',
    }
    for before,after in changes.items():
        if source.count(before)!=1:raise ValueError('Exact scanner reconciliation anchor required')
        source=source.replace(before,after)
    exec(compile(source,'snapshot-scan-reconciliation','exec'),{'__name__':'__main__'})

if __name__=='__main__':main()
