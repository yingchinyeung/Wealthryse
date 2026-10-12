"""Retire only the two manual reporting definitions explicitly approved by owner.

Reuse the archived retirement checks, never their historical seven-job scope.
"""
import hashlib
import json
from pathlib import Path
import sys
import retire_upgrade_additional_diagnostics_20261010 as r

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261011-stage39/legacy-reporting-retirement-v1'
IMAGES = {
    'ibkrv2stg-historical-reports': 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1e5322dc183e3e31ff780adcf2b8e8fdbc07ff1e7d50ddea8dbd421f5bc1d309',
    'ibkrv2stg-presentation-v23-hist': 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d6ae2566cd7f6a5071d559de4890f6bdbbdb3982279bf20fca5427878a6002cb',
}
AUTHORIZATION = 'Retire both after dependency checks'
r.APPROVED = frozenset(IMAGES)
r.OUT = OUT

def expected_image(name):
    r.path_for(name)
    return IMAGES[name]

r.expected_image = expected_image

def source_hashes():
    return {n: hashlib.sha256((ROOT / 'scripts' / n).read_bytes()).hexdigest() for n in
            (Path(__file__).name, 'retire_upgrade_additional_diagnostics_20261010.py',
             'plan_upgrade_owner_retirement_20261010.py', 'inspect_upgrade_owner_payloads_20261010.py')}

def main(stage):
    if stage == 'capture':
        for name in sorted(IMAGES):
            r.capture_one(name)
        print('Both definitions and available execution records archived; no deletion')
        return
    if stage != 'plan':
        raise ValueError('Only capture and dependency discovery enabled until reviewed')
    if (OUT / 'plan.json').exists():
        raise ValueError('Preserve earlier plan')
    entries = [r.protected(OUT / name / 'baseline.json') for name in sorted(IMAGES)]
    resources, dependencies, auxiliary, types = r.dependency_check()
    for entry in entries:
        r.current_target(entry)
    local = []
    for folder in ('src', 'scripts', 'infra'):
        for path in (ROOT / folder).rglob('*'):
            if any(p in ('node_modules', '__pycache__', '.venv') for p in path.parts):
                continue
            if path.suffix not in ('.py', '.json', '.bicep', '.yaml', '.yml'):
                continue
            hits = r.refs(path.read_text(encoding='utf-8', errors='replace'), IMAGES)
            if hits:
                local.append({'path': path.relative_to(ROOT).as_posix(),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'targets': hits})
    r.save(OUT, 'plan', {'targets': sorted(IMAGES), 'owner_authorization': AUTHORIZATION,
        'baseline_receipts': {e['name']: e['protected_receipt'] for e in entries},
        'non_target_bindings': r.bindings([x for x in resources if x['name'] not in IMAGES]),
        'dependencies': dependencies, 'auxiliary_callers': auxiliary, 'resource_type_counts': types,
        'local_references': local, 'sources': source_hashes(), 'dependency_review_complete': False,
        'production_database_writes': 0, 'reports_or_approvals_changed': False,
        'fourteen_day_observation': 'UNVERIFIED', 'real_viewer_sign_in': 'UNVERIFIED',
        'full_upgrade_complete': False})
    print(json.dumps({'dependencies': dependencies, 'local_references': local}))

if __name__ == '__main__':
    main(*sys.argv[1:])
