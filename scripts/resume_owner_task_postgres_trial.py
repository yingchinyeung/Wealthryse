"""One separately reviewed SQL rehearsal after accepted exact failed-setup cleanup.

The original and retry intents remain untouched. This fixed new attempt binds
the reviewed source checkpoint, unchanged bootstrap/baseline and create-only
protected intent. No automatic dispatch retry, model call, production migration,
new login, service permission or installed job/schedule change is permitted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import deploy_owner_task_postgres_trial as deploy
import run_owner_task_postgres_trial as wrapper
import verify_owner_task_source_checkpoint as qa

PRIOR_FOLDER = qa.FOLDER
FOLDER = deploy.ROOT/'.work/owner-task-postgres-20261003-r2'
CHECKPOINT_SHA = '565c1fac22a131203b3cd856a91e462a7821e2dc0cd9b24cd74304a6de0713f5'
CHECKPOINT_VERSION = '2026-10-03T09:12:50.2999170Z'
WORKSPACE = '5264a124-10cd-45f6-93a5-6d65c37e9bfb'


def validate_checkpoint(value,source):
    files = source.get('files')
    local = value.get('local_checks',{})
    if (type(files) is not dict or set(files) != set(deploy.FILES)
            or any(type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None for digest in files.values())
            or value.get('status') != 'validated-source-live-sql-pending'
            or any(value.get(k) is not True for k in ('synthetic_scope_removed','baseline_unchanged','source_only_wrapper_fix'))
            or any(value.get(k) is not False for k in ('production_migration_059_installed','owner_runtime_active',
                'six_specialist_runtimes_active','ibkr_source_writes','existing_service_grants_changed','natural_daily_cycle_verified'))
            or type(value.get('model_calls')) is not int or value['model_calls'] != 0
            or type(value.get('existing_jobs_unchanged')) is not int or value['existing_jobs_unchanged'] != 34
            or value.get('daily_schedule_hkt') != '18:00'
            or type(local) is not dict or type(local.get('test_count')) is not int or local['test_count'] != 1438
            or any(type(local.get(k)) is not int or local[k] != 0 for k in ('unittest_exit_code','compilation_exit_code'))
            or any(local.get(k) is not False for k in ('live_sql_tests_passed','financial_or_visual_acceptance',
                'os_or_dependency_security_certification'))
            or type(local.get('repository_policy')) is not str or not local['repository_policy'].startswith('PASS: ')
            or any(value.get('sources',{}).get(name) != digest for name,digest in files.items())
            or source.get('checkpoint_validator_sha256') != value.get('sources',{}).get('scripts/verify_owner_task_source_checkpoint.py')):
        raise ValueError('Exact reviewed source-only checkpoint and frozen SQL dependencies required')


def current_source(job,stage):
    wanted,source = deploy.template(job,stage)
    source.update(resume_dispatcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        checkpoint_validator_sha256=hashlib.sha256(Path(qa.__file__).read_bytes()).hexdigest(),
        candidate_manifest_sha256=deploy.digest(qa.source_manifest()))
    return wanted,source


def prerequisites(source):
    checkpoint_raw = json.loads((PRIOR_FOLDER/'source-checkpoint.json').read_text())
    if (checkpoint_raw.get('protected_receipt',{}).get('sha256') != CHECKPOINT_SHA
            or checkpoint_raw['protected_receipt'].get('version_id') != CHECKPOINT_VERSION):
        raise ValueError('Exact protected reviewed checkpoint required')
    checkpoint = deploy.protected(checkpoint_raw,'source-checkpoint')
    validate_checkpoint(checkpoint,source)
    cleanup_raw = json.loads((PRIOR_FOLDER/'failed-cleanup-acceptance.json').read_text())
    cleanup = deploy.protected(cleanup_raw,'failed-cleanup-accepted')
    expected = dict(status='passed',stage='failed-setup-cleanup',trial_database_removed=True,trial_role_removed=True,
        production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False,model_calls=0)
    record = cleanup.get('record',{})
    if (checkpoint['cleanup_receipt'] != cleanup_raw['protected_receipt'] or cleanup.get('status') != 'passed'
            or record != expected or any(type(record[k]) is not type(v) for k,v in expected.items())
            or cleanup.get('baseline_unchanged') is not True):
        raise ValueError('Accepted exact empty-scope cleanup required before a new rehearsal')
    original_raw = json.loads((PRIOR_FOLDER/'run-intent.json').read_text())
    original = deploy.protected(original_raw,'run-intent')
    if checkpoint['trial_intent_receipt'] != original_raw['protected_receipt']:
        raise ValueError('Protected checkpoint baseline intent binding differs')
    return checkpoint_raw['protected_receipt'],cleanup_raw['protected_receipt'],original['baseline'],original['bootstrap_settings_sha256']


def validate_run_record(record,source):
    fixed = dict(status='passed',stage='run',database=wrapper.DATABASE,trial_database=wrapper.DATABASE,
        schema=wrapper.SCHEMA,effective_role=wrapper.ROLE,trial_role=wrapper.ROLE,
        scope='synthetic_admin_context_effective_role_postgres_behavior',
        migration_sha256=source['files']['migrations/postgres/059_owner_codex_tasks.sql'],
        check_manifest_sha256=deploy.digest(deploy.EXPECTED_CHECKS),checks=deploy.EXPECTED_CHECKS)
    if (any(record.get(k) != v for k,v in fixed.items())
            or type(record.get('server_version')) is not str or not re.fullmatch(r'16\.[0-9]+(?: .*)?',record['server_version'])
            or any(record.get(k) is not False for k in ('fresh_login_verified','non_owner_privilege_boundary_verified',
                'protected_archive_live_verified','production_migration_installed','production_database_touched',
                'ibkr_source_writes','existing_service_grants_changed','owner_runtime_active','cleanup_performed'))
            or any(record.get(k) is not True for k in ('receipt_verifier_is_synthetic','trial_retained_for_review'))
            or any(type(record.get(k)) is not int or record[k] != 0 for k in
                ('source_rows_read','source_rows_written','service_grants_created','model_calls','agents_activated'))):
        raise ValueError('Complete synthetic SQL behavior acceptance required; no production boundary claim')


def validate_cleanup_record(record):
    expected = dict(status='passed',stage='cleanup',trial_database_removed=True,trial_role_removed=True,
        production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False)
    if record != expected or any(type(record[k]) is not type(v) for k,v in expected.items()):
        raise ValueError('Exact disposable cleanup absence readback required')


def stage_records(execution,run,wanted):
    if run.get('name') != execution:
        raise ValueError('Unexpected rehearsal execution identity')
    actual = run['properties']['template']
    if actual.get('volumes') or actual.get('initContainers'):
        raise ValueError('Unregistered rehearsal extension')
    deploy.verify_containers(actual['containers'],wanted)
    rows = deploy.az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+
        "' | where Log_s startswith '"+deploy.PREFIX+"' | project Log_s")
    return [json.loads(row['Log_s'].removeprefix(deploy.PREFIX)) for row in rows]


def assert_bootstrap_idle():
    """Fail closed unless every bounded inventory page contains terminal runs."""
    base = deploy.PATH+'/executions'
    path = base+'?api-version=2024-03-01'
    visited = set()
    for _ in range(100):
        if path in visited:
            raise ValueError('Repeated bootstrap inventory page')
        visited.add(path)
        page = deploy.request('GET',path)
        if type(page) is not dict:
            raise ValueError('Bootstrap inventory page malformed')
        runs = page.get('value')
        if type(runs) is not list or any(type(run) is not dict or
                type(run.get('properties')) is not dict or
                run['properties'].get('status') not in ('Succeeded','Failed','Stopped') for run in runs):
            raise ValueError('Bootstrap busy, unknown or inventory malformed')
        next_link = page.get('nextLink')
        if next_link is None or (type(next_link) is str and next_link == ''):
            return
        if type(next_link) is not str:
            raise ValueError('Unexpected bootstrap inventory continuation')
        parsed = urlsplit(next_link)
        if (parsed.scheme != 'https' or parsed.netloc != 'management.azure.com' or
                parsed.path != base or parsed.fragment or
                parse_qs(parsed.query).get('api-version') != ['2024-03-01']):
            raise ValueError('Unexpected bootstrap inventory continuation scope')
        path = parsed.path+'?'+parsed.query
    raise ValueError('Bootstrap inventory exceeded reviewed page bound')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage',choices=['prepare','start','inspect','verify','cleanup-start','cleanup-inspect','cleanup-verify'])
    parser.add_argument('--execution')
    args = parser.parse_args()
    stage = 'cleanup' if args.stage.startswith('cleanup-') else 'run'
    deploy.FOLDER = FOLDER
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted,source = current_source(job,stage)
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.stage == 'prepare':
        if (FOLDER/'preflight.json').exists():
            raise ValueError('Preflight reservation already exists; never overwrite evidence')
        checkpoint_receipt,cleanup_receipt,prior_baseline,prior_settings = prerequisites(source)
        local = qa.run_local_checks()
        if current_source(job,stage)[1] != source:
            raise ValueError('Sources changed during new-attempt verification')
        baseline = deploy.baseline()
        if (baseline.get('job_inventory_complete') is not True or len(baseline.get('other_jobs',{})) != 34
                or baseline != prior_baseline or deploy.settings(job) != prior_settings):
            raise ValueError('Complete accepted existing job inventory required')
        value = deploy.save(dict(status='prepared',source=source,template=wanted,local_checks=local,
            baseline=baseline,bootstrap_settings_sha256=deploy.settings(job),
            reviewed_checkpoint_receipt=checkpoint_receipt,accepted_cleanup_receipt=cleanup_receipt,
            stage='run',automatic_retry=False,production_migration_installed=False,ibkr_source_writes=False,
            model_calls=0,owner_runtime_active=False,six_specialist_runtimes_active=False),
            'preflight.json','reviewed-resume-preflight')
        deploy.protected(value,'reviewed-resume-preflight')
        if current_source(job,stage)[1] != source:
            raise ValueError('Sources changed during protected preflight retention')
        print(json.dumps(dict(status='prepared',tests=local['test_count'],protected_receipt=value['protected_receipt']),sort_keys=True),flush=True)
        return
    preflight_raw = json.loads((FOLDER/'preflight.json').read_text())
    preflight = deploy.protected(preflight_raw,'reviewed-resume-preflight')
    if (preflight.get('status') != 'prepared' or preflight['source'] != source
            or preflight['bootstrap_settings_sha256'] != deploy.settings(job)):
        raise ValueError('Frozen reviewed preflight/source/bootstrap settings differ')
    ip = FOLDER/(stage+'-intent.json'); dp = FOLDER/(stage+'-dispatch.json')
    if args.stage in ('start','cleanup-start'):
        if ip.exists() or dp.exists():
            raise ValueError('Intent already exists; never automatically redispatch')
        checkpoint_receipt,cleanup_receipt,prior_baseline,prior_settings = prerequisites(source)
        if (preflight['reviewed_checkpoint_receipt'] != checkpoint_receipt
                or preflight['accepted_cleanup_receipt'] != cleanup_receipt
                or preflight['baseline'] != prior_baseline or deploy.settings(job) != prior_settings):
            raise ValueError('Preflight prerequisite bindings differ')
        if stage == 'cleanup':
            accepted = deploy.protected(json.loads((FOLDER/'run-acceptance.json').read_text()),'reviewed-resume-run-accepted')
            if accepted.get('status') != 'passed' or accepted['source'] != source:
                raise ValueError('Exact new SQL acceptance required before synthetic cleanup')
            validate_run_record(accepted['record'],source)
        baseline = deploy.baseline()
        if baseline != preflight['baseline']:
            raise ValueError('Accepted website/authentication and existing job definitions changed')
        assert_bootstrap_idle()
        intent = deploy.save(dict(stage=stage,source=source,template=wanted,baseline=baseline,
            preflight_receipt=preflight_raw['protected_receipt'],
            bootstrap_settings_sha256=deploy.settings(job),automatic_retry=False,
            scope='exact-marked-synthetic-database-and-NOLOGIN-role',production_database_touched=False,
            ibkr_source_writes=False,existing_service_grants_changed=False,model_calls=0),
            ip.name,'reviewed-resume-'+stage+'-intent')
        deploy.protected(intent,'reviewed-resume-'+stage+'-intent')
        if current_source(job,stage)[1] != source or deploy.baseline() != baseline:
            raise ValueError('Source or accepted baseline changed after protected intent; do not retry')
        run = deploy.request('POST',deploy.PATH+'/start?api-version=2024-03-01',data=wanted)
        value = deploy.save(dict(stage=stage,source=source,execution=run['name'],
            intent_receipt=intent['protected_receipt']),dp.name,'reviewed-resume-'+stage+'-dispatch')
        print(json.dumps(dict(execution=value['execution'],stage=stage,protected_intent_verified=True),sort_keys=True),flush=True)
        return
    intent_raw = json.loads(ip.read_text())
    intent = deploy.protected(intent_raw,'reviewed-resume-'+stage+'-intent')
    dispatch = deploy.protected(json.loads(dp.read_text()),'reviewed-resume-'+stage+'-dispatch')
    if (args.execution != dispatch['execution'] or not re.fullmatch(deploy.JOB+r'-[a-z0-9]{7}',args.execution or '')
            or intent['source'] != source or dispatch['source'] != source or intent['stage'] != stage
            or dispatch['stage'] != stage or dispatch['intent_receipt'] != intent_raw['protected_receipt']
            or intent['preflight_receipt'] != preflight_raw['protected_receipt']
            or intent['bootstrap_settings_sha256'] != deploy.settings(job)):
        raise ValueError('Exact execution, source and protected intent linkage required')
    run = deploy.request('GET',deploy.PATH+'/executions/'+args.execution+'?api-version=2024-03-01')
    records = stage_records(args.execution,run,wanted)
    if args.stage in ('inspect','cleanup-inspect'):
        print(json.dumps(dict(execution=args.execution,status=run['properties']['status'],records=records,
            executed_source_verified=True),sort_keys=True),flush=True)
        return
    if run['properties']['status'] != 'Succeeded' or len(records) != 1:
        raise ValueError('Successful exact rehearsal record unavailable; inspect without retry')
    if stage == 'run':
        validate_run_record(records[0],source)
    else:
        validate_cleanup_record(records[0])
    if deploy.baseline() != intent['baseline'] or current_source(job,stage)[1] != source:
        raise ValueError('Rehearsal source or accepted service definitions changed')
    value = deploy.save(dict(status='passed',execution=args.execution,source=source,record=records[0],
        preflight_receipt=preflight_raw['protected_receipt'],intent_receipt=intent_raw['protected_receipt'],
        baseline_unchanged=True,existing_jobs_unchanged=34,daily_schedule_hkt='18:00',
        owner_runtime_active=False,six_specialist_runtimes_active=False,production_migration_installed=False),
        stage+'-acceptance.json','reviewed-resume-'+stage+'-accepted')
    deploy.protected(value,'reviewed-resume-'+stage+'-accepted')
    if current_source(job,stage)[1] != source:
        raise ValueError('Source changed during protected acceptance readback')
    print(json.dumps(dict(status='passed',stage=stage,execution=args.execution,
        checks=records[0].get('checks',[]),protected_receipt=value['protected_receipt'],
        owner_runtime_active=False,six_specialist_runtimes_active=False),sort_keys=True),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(status='stopped',error_type=type(error).__name__,automatic_retry=False),sort_keys=True),flush=True)
        raise SystemExit(1)
