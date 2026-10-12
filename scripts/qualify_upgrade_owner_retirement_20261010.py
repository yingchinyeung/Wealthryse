"""Resolve one inert metadata reference; preserve the original blocked plan."""
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
from plan_upgrade_owner_retirement_20261010 import OUT, ROOT, API, request, rid, settings_invariant, protected, save, load_plan
import qualify_owner_task_catalog_canary as canary


def main():
    if (OUT / 'dependency-qualified.json').exists(): raise ValueError('Preserve existing qualification')
    plan = load_plan()
    expected = [{'caller': 'ibkrv2stg-ownertrial-log-canary', 'targets': ['ibkrv2stg-ownertrial-catalog'], 'kind': 'app_or_job_definition'}]
    if plan['dependencies'] != expected: raise ValueError('Unreviewed dependency')
    job = request('GET', rid('Microsoft.App/jobs', expected[0]['caller']) + API)
    args, payload_sha = canary.bundle()
    c = job['properties']['template']['containers'][0]
    if c['args'] != args or c['command'] != ['python'] or c.get('env') or settings_invariant(job) != plan['non_target_bindings'][job['name']]['invariant']: raise ValueError('Canary source/settings drift')
    if job['properties']['configuration']['triggerType'] != 'Manual': raise ValueError('Unexpected canary schedule')
    tests = ('test_owner_codex_isolated_target.py', 'test_owner_codex_catalog_transport.py', 'test_owner_task_catalog_canary.py')
    count = 0; logs = []
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'scripts'))), 'PYTHONDONTWRITEBYTECODE': '1'}
    for test in tests:
        run = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', test, '-v'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        log = run.stdout + run.stderr; logs.append(log)
        if run.returncode: print(log[-2000:]); raise ValueError('Inert-canary boundary tests failed')
        count += int(re.findall(r'Ran (\d+) tests', log)[-1])
    log = '\n'.join(logs)
    with (OUT / 'dependency-tests.log').open('x', encoding='utf-8') as f: f.write(log)
    record = {'passed': True, 'plan_receipt': plan['protected_receipt'], 'resolved_reference': expected[0],
        'reason': 'Exact installed payload equals the reviewed stdout-only synthetic transport canary. The catalog job name is inert target-registration metadata; no catalog job request, database connection, credential lookup or Azure SDK call is made. Canary definition is retained unchanged.',
        'canary_args_sha256': hashlib.sha256(json.dumps(args).encode()).hexdigest(), 'canary_payload_sha256': payload_sha,
        'tests': count, 'test_log_sha256': hashlib.sha256(log.encode()).hexdigest(),
        'local_reference_classification': '36 references retained: explicit historical diagnostic controllers, inert target registration and read-only upgrade/audit scripts. No website import of these diagnostic modules or scheduled launch found.',
        'source_files': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in canary.FILES},
        'authorization': plan['owner_authorization'], 'deleted': False}
    save(OUT, 'dependency-qualified', record)
    print(json.dumps({'passed': True, 'tests': count, 'resolved_inert_references': 1, 'deleted': False}))


if __name__ == '__main__': main()
