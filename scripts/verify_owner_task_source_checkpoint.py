"""Local regression + protected historical evidence + unchanged Azure baseline.

This is a source checkpoint, not successful live PostgreSQL qualification or
runtime activation. No database/model calls, dispatches or settings changes.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import deploy_owner_task_postgres_trial as deploy

FOLDER = deploy.ORIGINAL_FOLDER.with_name(deploy.ORIGINAL_FOLDER.name+'-retry1')
DOCUMENT = 'docs/APPROVED_UPGRADES_EXECUTION_2026-10-01.md'
FAILED_TRIAL = 'ibkrv2stg-bootstrap-85v8wm8'


def source_manifest():
    paths = []
    for name in ('src','scripts','tests','migrations'):
        paths.extend(p for p in (deploy.ROOT/name).rglob('*') if p.is_file() and p.suffix in ('.py','.sql'))
    paths.append(deploy.ROOT/DOCUMENT)
    return {p.relative_to(deploy.ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))}


def validate_regression_result(result):
    """Accept success and record only the explicitly known OS fixture limitation.

    Any unregistered skipped test, expected failure, unexpected success or
    failure blocks this checkpoint. A permitted skip is recorded, not a pass.
    """
    counts = re.findall(r'^Ran (\d+) tests? in [0-9.]+s$', result.stderr, re.MULTILINE)
    summaries = re.findall(r'^OK(?: \(skipped=([1-9][0-9]*)\))?$', result.stderr, re.MULTILINE)
    skipped = re.findall(r"^\w+ \(([^)]+)\) \.\.\. skipped '([^']+)'$", result.stderr, re.MULTILINE)
    allowed = ({'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_symlink_entry_never_followed':
        'OS does not permit unprivileged test symlink creation',
        'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_fifo_entry_is_rejected_without_blocking':
        'POSIX FIFO fixture'} if os.name == 'nt' else
        {'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_windows_junction_entry_is_rejected':
        'Windows junction fixture',
        'test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_unc_and_mapped_drive_roots_rejected_before_filesystem_access':
        'Windows drive metadata fixture'})
    expected_skips = 0 if summaries == [''] else int(summaries[0]) if len(summaries) == 1 else -1
    if (result.returncode != 0 or len(counts) != 1 or int(counts[0]) < 1 or len(summaries) != 1
            or len(skipped) != expected_skips or len(set(skipped)) != len(skipped)
            or any(allowed.get(test) != reason for test, reason in skipped)
            or re.search(r'^(?:FAILED|ERROR:|FAIL:|UNEXPECTED SUCCESS:)', result.stderr, re.MULTILINE)):
        raise ValueError('Full local regression failed or has unreviewed skips; no source checkpoint retained')
    if os.name == 'nt' and skipped and not re.search(
            r'^test_actual_windows_junction_entry_is_rejected '
            r'\(test_owner_codex_trial_journal_process.LocalJournalProcessTests.test_actual_windows_junction_entry_is_rejected\) \.\.\. ok$',
            result.stderr, re.MULTILINE):
        raise ValueError('Actual Windows junction rejection was not exercised')
    return int(counts[0]), [{'test': test, 'reason': reason, 'result': 'not_exercised'} for test, reason in skipped]


def run_local_checks():
    result = subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],
        cwd=deploy.ROOT,capture_output=True,text=True,timeout=300)
    count, skipped = validate_regression_result(result)
    environment = dict(os.environ,GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',
        GIT_CONFIG_VALUE_0=deploy.ROOT.as_posix())
    policy = subprocess.run([sys.executable,'scripts/ci_policy_check.py'],cwd=deploy.ROOT,
        env=environment,capture_output=True,text=True,timeout=90)
    if policy.returncode != 0 or not policy.stdout.startswith('PASS: '):
        raise ValueError('Repository policy failed; no checkpoint retained')
    compiled = subprocess.run([sys.executable,'-m','compileall','-q','-x','node_modules','src','scripts','tests'],
        cwd=deploy.ROOT,capture_output=True,text=True,timeout=90)
    if compiled.returncode != 0:
        raise ValueError('Compilation failed; no checkpoint retained')
    return dict(test_count=count,passed_test_count=count-len(skipped),skipped_tests=skipped,
        unittest_exit_code=0,compilation_exit_code=0,
        repository_policy=policy.stdout.strip(),live_sql_tests_passed=False,
        financial_or_visual_acceptance=False,os_or_dependency_security_certification=False)


def validate_history(original_raw,original,dispatch,metadata_raw,metadata,cleanup):
    expected = dict(status='passed',stage='failed-setup-cleanup',trial_database_removed=True,trial_role_removed=True,
        production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False,model_calls=0)
    record = cleanup.get('record',{})
    baseline = original.get('baseline',{})
    if (original.get('stage') != 'run' or original.get('startup_retry') is not True
            or baseline.get('job_inventory_complete') is not True
            or type(baseline.get('other_jobs')) is not dict or len(baseline['other_jobs']) != 34
            or dispatch.get('stage') != 'run' or dispatch.get('execution') != FAILED_TRIAL
            or dispatch.get('intent_receipt') != original_raw['protected_receipt']
            or dispatch.get('source') != original['source']
            or metadata.get('status') != 'observed' or metadata.get('failed_trial') != FAILED_TRIAL
            or metadata.get('baseline_unchanged') is not True or cleanup.get('status') != 'passed'
            or record != expected or any(type(record[k]) is not type(value) for k,value in expected.items())
            or cleanup.get('metadata_receipt') != metadata_raw['protected_receipt']
            or metadata.get('source',{}).get('files') != original['source']['files']
            or cleanup.get('source',{}).get('files') != original['source']['files']
            or cleanup.get('baseline_unchanged') is not True):
        raise ValueError('Protected failed rehearsal, complete job inventory and exact cleanup bindings differ')
    return len(baseline['other_jobs'])


def main():
    deploy.FOLDER = FOLDER
    if (FOLDER/'source-checkpoint.json').exists():
        raise ValueError('Source checkpoint already exists; never overwrite evidence')
    before = source_manifest()
    local = run_local_checks()
    if source_manifest() != before:
        raise ValueError('Sources changed during local verification')
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    original_raw = json.loads((FOLDER/'run-intent.json').read_text())
    original = deploy.protected(original_raw,'run-intent')
    dispatch_raw = json.loads((FOLDER/'run-dispatch.json').read_text())
    dispatch = deploy.protected(dispatch_raw,'run-dispatch')
    metadata_raw = json.loads((FOLDER/'metadata-evidence.json').read_text())
    metadata = deploy.protected(metadata_raw,'metadata-observed')
    cleanup_raw = json.loads((FOLDER/'failed-cleanup-acceptance.json').read_text())
    cleanup = deploy.protected(cleanup_raw,'failed-cleanup-accepted')
    count = validate_history(original_raw,original,dispatch,metadata_raw,metadata,cleanup)
    # Only the reviewed trusted trial wrapper changed since historical cleanup.
    # This does not rewrite, recertify or consume the old trial as a new success.
    changed = [name for name,digest in original['source']['files'].items()
        if before.get(name) != digest]
    if changed != ['scripts/run_owner_task_postgres_trial.py']:
        raise ValueError('Unreviewed live-trial dependency change')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    deploy.validate_bootstrap(job)
    if deploy.settings(job) != original['bootstrap_settings_sha256'] or deploy.baseline() != original['baseline']:
        raise ValueError('Website/authentication or existing job definitions changed')
    if source_manifest() != before:
        raise ValueError('Sources changed during protected/Azure checks')
    accepted = deploy.save(dict(status='validated-source-live-sql-pending',
        checked_at_utc=datetime.now(timezone.utc).isoformat(),sources=before,local_checks=local,
        failed_trial=FAILED_TRIAL,trial_intent_receipt=original_raw['protected_receipt'],
        trial_dispatch_receipt=dispatch_raw['protected_receipt'],metadata_receipt=metadata_raw['protected_receipt'],
        cleanup_receipt=cleanup_raw['protected_receipt'],synthetic_scope_removed=True,
        baseline_unchanged=True,existing_jobs_unchanged=count,daily_schedule_hkt='18:00',
        natural_daily_cycle_verified=False,production_migration_059_installed=False,
        owner_runtime_active=False,six_specialist_runtimes_active=False,ibkr_source_writes=False,
        existing_service_grants_changed=False,model_calls=0,source_only_wrapper_fix=True,
        historically_independently_reviewed_worker_test_count=48),
        'source-checkpoint.json','source-checkpoint')
    deploy.protected(accepted,'source-checkpoint')
    if source_manifest() != before:
        raise ValueError('Sources changed during final protected checkpoint readback; review retained evidence')
    print(json.dumps(dict(status=accepted['status'],tests=local['test_count'],
        protected_receipt=accepted['protected_receipt'],owner_runtime_active=False,
        six_specialist_runtimes_active=False,live_sql_qualification='pending'),sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
