"""Read-only live acceptance plus local tests; retain only hashes/status evidence.

No deployments, migration installation, grants, model requests or job starts.
The archive receipt is not a production runtime or full activity-ledger grant.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from audit_azure_runtime import az
from deploy_metadata_monitor import BASE, SUB, request
from deploy_codex_exec_probe import protected_record, validate_result
from prepare_codex_exec_probe import ROOT, FOLDER, manifest
from deploy_specialist_daily_schedule import unaffected, validate as validate_daily, bundle
from deploy_specialist_report_worker import JOB, job_path
from deploy_specialist_report_watchdog import RULE_PATH, wanted
from deploy_agent_report_notifications import fingerprint, validate_rule
from run_specialist_daily_publication import prerequisites, REVISION
from run_specialist_gateway_read_verification import retain


def local_check(arguments, environment):
    result = subprocess.run([sys.executable, *arguments], cwd=ROOT,
        env=environment, capture_output=True, timeout=240)
    output = result.stdout + result.stderr
    if result.returncode:
        raise ValueError('Local acceptance check failed: '+arguments[0])
    return output


def code_tree():
    files = {}
    for name in ('src', 'scripts', 'tests', 'migrations'):
        for path in (ROOT/name).rglob('*'):
            if (path.is_file() and path.suffix in {'.py', '.sql'}
                and not {'node_modules', '__pycache__'} & set(path.relative_to(ROOT).parts)):
                files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
    return dict(files=len(files), sha256=hashlib.sha256(payload).hexdigest())


def daily_fingerprint(job):
    # Match the earlier protected QA's exact projection. ARM response status,
    # addresses and endpoints are not execution settings. The separate typed
    # definition validator also checks environment, tags and schedule controls.
    return fingerprint(dict(identity=job['identity'],
        configuration=job['properties']['configuration'], template=job['properties']['template']))


def main():
    if az('account', 'show', '--query', 'id') != SUB:
        raise ValueError('Unexpected subscription')
    record = manifest()[1]
    folder = FOLDER/record['source_manifest_sha256'][:12]
    if (folder/'final-qa.json').exists():
        raise ValueError('Final QA already retained; inspect instead of replacing')
    accepted = protected_record(folder/'azure-acceptance.json', 'passed')
    intent = protected_record(folder/'azure-intent.json', 'intent')
    dispatch = protected_record(folder/'azure-dispatch.json', 'dispatched')
    if (accepted['source'] != record or accepted['image'] != intent['image']
        or accepted['image'] != dispatch['image'] or accepted['execution'] != dispatch['execution']):
        raise ValueError('Accepted source differs from current source')
    validate_result(accepted['runtime'])
    paths = ('src/ibkr_etl/owner_codex_tasks.py', 'migrations/postgres/059_owner_codex_tasks.sql',
        'tests/test_owner_codex_tasks.py', 'tests/test_owner_codex_postgres_contract.py',
        'docs/APPROVED_UPGRADES_EXECUTION_2026-10-01.md', 'scripts/verify_codex_exec_checkpoint.py')
    hashes = lambda: {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths}
    before_hashes = hashes()
    before_tree = code_tree()

    environment = dict(os.environ, GIT_CONFIG_COUNT='1',
        GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=str(ROOT).replace('\\', '/'))
    output = local_check(['-m', 'unittest', 'discover', '-s', 'tests'], environment)
    match = re.search(rb'Ran ([0-9]+) tests in [0-9.]+s\r?\n\r?\nOK(?:\r?\n|$)', output)
    if not match:
        raise ValueError('Exact unittest success record missing')
    count = int(match[1])
    policy = local_check(['scripts/ci_policy_check.py'], environment)
    if not policy.startswith(b'PASS: '):
        raise ValueError('Source policy acceptance missing')
    compiled = local_check(['-m', 'compileall', '-q', '-x', 'node_modules', 'src', 'scripts', 'tests'], environment)

    baseline = unaffected()
    if baseline != intent['baseline'] or len(baseline['other_jobs']) != 34:
        raise ValueError('Existing workload configuration changed')
    daily = request('GET', job_path(JOB)+'?api-version=2024-03-01')
    validate_daily(daily, True)
    if (daily_fingerprint(daily) != '21045e81dc0ddcbd7dd03f569ab4c46b853dfbd28b238a9c9facf1c3ef2e70ff'
        or bundle()[1] != 'd2a920dc5c14cb7f9d5fa97bf49d4513646c52fdbef80376819a3a5454ea8e34'):
        raise ValueError('Accepted daily reporting configuration changed')
    watchdog = request('GET', RULE_PATH+'?api-version=2023-12-01')
    validate_rule(watchdog, wanted(True))
    if fingerprint(watchdog['properties']) != 'eed0c117bc65e6e66480faa8d1e8bdfe30491bcf50164fffbf0f0ae035939fea':
        raise ValueError('Accepted watchdog changed')
    prerequisites()  # Exact accepted website image, ready revision and auth hash.
    revision = request('GET', BASE+'Microsoft.App/containerApps/ibkrv2stg-web/revisions/'+REVISION+'?api-version=2024-03-01')
    p = revision['properties']
    if p.get('healthState') != 'Healthy' or p.get('runningState') != 'Running' or p.get('active') is not True:
        raise ValueError('Accepted website revision unhealthy')
    if manifest()[1] != record or hashes() != before_hashes or code_tree() != before_tree:
        raise ValueError('Acceptance source changed during checks')
    value = dict(status='passed', source_manifest_sha256=record['source_manifest_sha256'],
        image=accepted['image'], execution=accepted['execution'], runtime_receipt=accepted['protected_receipt'],
        tests=count, test_output_sha256=hashlib.sha256(output).hexdigest(),
        source_policy_passed=True, source_policy_output_sha256=hashlib.sha256(policy).hexdigest(),
        compilation_passed=True, compilation_output_sha256=hashlib.sha256(compiled).hexdigest(),
        owner_foundation_source_hashes=before_hashes,
        tested_python_sql_tree=before_tree,
        owner_task_postgres_live_acceptance=False, migration_059_installed=False,
        website_revision=REVISION, website_healthy=True, website_auth_unchanged=True,
        other_jobs_unchanged=34, daily_schedule_unchanged=True, daily_hkt='18:00',
        daily_settings_sha256=daily_fingerprint(daily),
        watchdog_unchanged=True, first_natural_daily_execution_verified=False,
        owner_runtime_active=False, six_specialist_runtimes_active=False,
        full_activity_capture_active=False, production_accepted=False,
        ibkr_source_writes=False, database_or_permission_mutations=False)
    value['protected_receipt'] = retain(value, 'codex-exec-final-qa')
    (folder/'final-qa.json').write_text(json.dumps(value, sort_keys=True, indent=2))
    print(json.dumps(value, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
