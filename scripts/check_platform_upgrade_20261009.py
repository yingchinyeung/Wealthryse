"""Local bundle integrity/readiness report. No deployment capability."""
import json
from pathlib import Path
import sys
from qualify_platform_upgrade_20261009 import OUT, manifest, sha


def check():
    bundle=json.loads((OUT/'bundle-manifest.json').read_bytes())
    gates=json.loads((OUT/'release-gates.json').read_bytes())
    qualification=json.loads((OUT/'evidence/local-qualification.json').read_bytes())
    checks={
        'application_context':manifest(OUT/'build/application')==bundle['build_context_sha256'],
        'infrastructure':manifest(OUT/'infra')==bundle['infrastructure_sha256'],
        'source_patch':sha(OUT/'candidate.patch')==bundle['source_patch_sha256'],
        'qualification':sha(OUT/'evidence/local-qualification.json')==bundle['local_qualification_sha256'],
        'gate_definition':sha(OUT/'release-gates.json')==bundle['release_gates_sha256'],
        'worker':sha(OUT/'worker/generate_month_end_package.py')==bundle['worker_candidate_sha256'],
        'runtime':manifest(OUT/'runtime')==qualification['runtime_sha256'],
        'tests':manifest(OUT/'tests')==qualification['tests_sha256'],
        'test_log':sha(OUT/'evidence/local-tests.log')==qualification['log_sha256'],
    }
    pending=[gate['id'] for gate in gates['required_acceptance'] if gate['status']!='passed']
    return {'integrity_passed':all(checks.values()),'checks':checks,
            'tests_run':qualification['tests_run'],
            'pending_gates':pending,
            'production_deployable':all(checks.values()) and not pending and gates['deployable'] is True,
            'live_changes':False}


if __name__=='__main__':
    result=check()
    print(json.dumps(result,indent=2))
    if not result['integrity_passed']:raise SystemExit(1)
    if '--integrity-only' not in sys.argv and not result['production_deployable']:raise SystemExit(2)
