"""Additive descriptive-default correction for the SAME unconsumed SQL trial.

The 760-source preflight, operation, physical target and reservations are never
reset or rewritten. Only Azure's empty ephemeralStorage descriptor is accepted;
nonempty changes fail. Protected review and fresh QA bind the two new sources
before any effect. Frozen predecessor modules/globals remain untouched.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import deploy_owner_task_isolated_sql as prior

ROOT, FOLDER, worker, target = prior.ROOT, prior.FOLDER, prior.worker, prior.target
ADDITIONS = frozenset(('scripts/owner_task_isolated_sql_readback.py', 'tests/test_owner_task_isolated_sql_readback.py'))
PREFLIGHT_SHA = 'bb17fc84ca6354c3d8ecf46710ffee37f431768351b43520889e278d22629466'


def require(value, code):
    if not value: raise ValueError(code)


def validate_sources(before, after):
    require(type(before) is dict and len(before) == 760 and type(after) is dict
        and set(after) == set(before) | ADDITIONS and not set(before) & ADDITIONS
        and all(after[k] == v for k, v in before.items())
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in after.items()),
        'isolated_sql_readback_sources_invalid')


def validate_job(value, plan, identity):
    shadow = prior.compatibility.copy(value)
    containers = shadow.get('properties', {}).get('template', {}).get('containers')
    require(type(containers) is list and len(containers) == 1, 'isolated_sql_readback_invalid')
    resources = containers[0].get('resources')
    require(type(resources) is dict, 'isolated_sql_readback_invalid')
    if 'ephemeralStorage' in resources:
        require(type(resources['ephemeralStorage']) is str and resources['ephemeralStorage'] == '',
            'isolated_sql_readback_invalid')
        del resources['ephemeralStorage']
    result = prior.validate_job(shadow, plan, identity)
    return dict(result, native_job_sha256=prior.digest(value), detached_shadow_sha256=prior.digest(shadow),
        only_empty_ephemeral_storage_descriptor_removed=shadow != value)


def checked_preflight():
    raw, pf = prior.load('preflight.json')
    require(raw['protected_receipt']['sha256'] == PREFLIGHT_SHA
        and pf['status'] == 'prepared_private_sql_trial' and pf['operation_id'] == worker.OPERATION,
        'isolated_sql_readback_preflight_required')
    source = {k: (ROOT / k).read_bytes() for k in prior.launch.SOURCE_PATHS}
    expected = prior.launch.build(source, {k: hashlib.sha256(v).hexdigest() for k, v in source.items()}, pf['plan']['expected_runtime'])
    require(expected == pf['plan'], 'isolated_sql_readback_launch_changed')
    return raw, pf


def run_local_checks():
    """Explicit new-review deadline; frozen QA/skip contracts remain unchanged.

    The complete predecessor suite exceeded 300 seconds during the large
    synthetic log reconstruction tests. No partial results, missing tests or
    new skips count as success. This changes no Azure execution deadline.
    """
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    count, skipped = prior.qa.validate_regression_result(result)
    environment = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory',
        GIT_CONFIG_VALUE_0=ROOT.as_posix())
    policy = subprocess.run([sys.executable, 'scripts/ci_policy_check.py'], cwd=ROOT,
        env=environment, capture_output=True, text=True, timeout=90)
    require(policy.returncode == 0 and policy.stdout.startswith('PASS: '), 'isolated_sql_repository_policy_failed')
    compiled = subprocess.run([sys.executable, '-m', 'compileall', '-q', '-x', 'node_modules', 'src', 'scripts', 'tests'],
        cwd=ROOT, capture_output=True, text=True, timeout=90)
    require(compiled.returncode == 0, 'isolated_sql_compilation_failed')
    return dict(test_count=count, passed_test_count=count-len(skipped), skipped_tests=skipped,
        unittest_exit_code=0, compilation_exit_code=0, repository_policy=policy.stdout.strip(),
        regression_deadline_seconds=600, policy_deadline_seconds=90, compilation_deadline_seconds=90,
        complete_discovery_required=True, live_sql_tests_passed=False,
        financial_or_visual_acceptance=False, os_or_dependency_security_certification=False)


def prepare():
    require(not (FOLDER / 'readback-review.json').exists(), 'isolated_sql_readback_review_consumed')
    raw, pf = checked_preflight(); before = prior.host.sources(); validate_sources(pf['sources'], before)
    # No stage may have been consumed before this compatibility review.
    require(not any((FOLDER / (stage + '-intent.json')).exists() for stage in prior.STAGES),
        'isolated_sql_readback_effect_already_consumed')
    prior.host.checked_owner()
    checks = run_local_checks()
    require(prior.host.sources() == before and prior.baseline() == pf['baseline']
        and prior.host.observe(prior.host.Transport()) == pf['host'], 'isolated_sql_readback_drift')
    return prior.save(dict(status='empty_resource_descriptor_reviewed', operation_id=worker.OPERATION,
        sources=before, previous_preflight_receipt=raw['protected_receipt'], local_checks=checks,
        checked_at_utc=prior.now(), new_dispatch_scope=False, step_2_complete=False), 'readback-review.json')


def guards():
    raw, pf = checked_preflight(); review_raw, review = prior.load('readback-review.json')
    require(review['status'] == 'empty_resource_descriptor_reviewed' and review['operation_id'] == worker.OPERATION
        and review['previous_preflight_receipt'] == raw['protected_receipt']
        and review['new_dispatch_scope'] is False and review['step_2_complete'] is False,
        'isolated_sql_readback_review_invalid')
    validate_sources(pf['sources'], review['sources'])
    require(prior.host.sources() == review['sources'], 'isolated_sql_readback_source_drift')
    prior.host.checked_owner()
    require(prior.baseline() == pf['baseline'] and prior.host.observe(prior.host.Transport()) == pf['host'],
        'isolated_sql_readback_live_drift')
    return raw, pf, review_raw


def dispatch(client, stage):
    require(stage in prior.STAGES and not any((FOLDER / (stage + '-' + suffix + '.json')).exists()
        for suffix in ('intent', 'ack', 'accepted')), 'isolated_sql_stage_consumed_inspect_only')
    raw, pf, review = guards()
    previous_stages = prior.STAGES[:prior.STAGES.index(stage)]
    for previous in previous_stages:
        _, accepted = prior.load(previous + '-accepted.json')
        require(accepted['status'] == 'private_sql_effect_verified', 'isolated_sql_prior_effect_unaccepted')
    identity = None
    if stage != 'identity':
        identity = prior.live_identity(client.call('GET', prior.MI, '2023-01-31'))
        prior.roles(client, identity['principalId'], [s for s in previous_stages if s in prior.ROLES])
    plan = pf['plan']; method, path, api, body = prior.effect(stage, plan, None if identity is None else identity['principalId'])
    if stage == 'start':
        validate_job(client.call('GET', prior.JOB, '2025-07-01'), plan, identity)
        inventory = client.call('GET', prior.JOB + '/executions', '2025-07-01')
        require(inventory.get('value') == [] and inventory.get('nextLink') in (None, ''), 'isolated_sql_execution_already_exists')
    else: require(client.call('GET', path, api, missing=True) is None, 'isolated_sql_effect_target_exists')
    value = dict(operation_id=worker.OPERATION, stage=stage, preflight_receipt=raw['protected_receipt'],
        readback_review_receipt=review['protected_receipt'], method=method, path=path, api=api, body=body, automatic_retry=False)
    intent = prior.save(dict(value, reservation=prior.reserve(stage, value)), stage + '-intent.json')
    guards()  # Fresh protected source/live guards AFTER intent, BEFORE effect.
    if stage != 'start': require(client.call('GET', path, api, missing=True) is None, 'isolated_sql_effect_target_exists')
    else:
        validate_job(client.call('GET', prior.JOB, '2025-07-01'), plan, identity)
        inventory = client.call('GET', prior.JOB + '/executions', '2025-07-01')
        require(inventory.get('value') == [] and inventory.get('nextLink') in (None, ''), 'isolated_sql_execution_already_exists')
    ack = client.call(method, path, api, body=body, stage=stage, plan=plan,
        principal=None if identity is None else identity['principalId'])
    return prior.save(dict(status='private_sql_effect_acknowledged_inspect_required', stage=stage,
        operation_id=worker.OPERATION, intent_receipt=intent['protected_receipt'], raw_ack=ack,
        attempt_consumed=True, automatic_retry=False, checked_at_utc=prior.now()), stage + '-ack.json')


def inspect(client, stage):
    raw, pf, review = guards(); ack_raw, ack = prior.load(stage + '-ack.json'); intent_raw, intent = prior.load(stage + '-intent.json')
    require(ack['intent_receipt'] == intent_raw['protected_receipt'] and intent['preflight_receipt'] == raw['protected_receipt']
        and intent['readback_review_receipt'] == review['protected_receipt'] and intent['stage'] == stage
        and intent['operation_id'] == worker.OPERATION and ack['attempt_consumed'] is True,
        'isolated_sql_chain_invalid')
    identity = prior.live_identity(client.call('GET', prior.MI, '2023-01-31'))
    inventory = prior.roles(client, identity['principalId'], [s for s in prior.STAGES[:prior.STAGES.index(stage)+1] if s in prior.ROLES])
    if stage == 'job': validate_job(client.call('GET', prior.JOB, '2025-07-01'), pf['plan'], identity)
    elif stage == 'start':
        execution = prior.execution_name(ack['raw_ack'].get('name'))
        require(ack['raw_ack'].get('id') == prior.JOB + '/executions/' + execution, 'isolated_sql_execution_invalid')
        observation = client.call('GET', prior.JOB + '/executions/' + execution, '2025-07-01')
        return dict(status='private_sql_execution_observed', execution=execution, native=observation, step_2_complete=False)
    return prior.save(dict(status='private_sql_effect_verified', stage=stage, operation_id=worker.OPERATION,
        preflight_receipt=raw['protected_receipt'], readback_review_receipt=review['protected_receipt'],
        ack_receipt=ack_raw['protected_receipt'], identity=identity, role_inventory=inventory,
        checked_at_utc=prior.now(), step_2_complete=False), stage + '-accepted.json')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=('prepare', 'dispatch', 'inspect'))
    parser.add_argument('--stage', choices=prior.STAGES); args = parser.parse_args()
    try:
        client = prior.Transport()
        result = prepare() if args.command == 'prepare' else dispatch(client, args.stage) if args.command == 'dispatch' else inspect(client, args.stage)
        print(json.dumps({k: result[k] for k in ('status', 'stage', 'execution', 'protected_receipt', 'native') if k in result}, sort_keys=True))
    except Exception as error:
        code = str(error); code = code if re.fullmatch('isolated_sql_[a-z_]+', code) else 'isolated_sql_unaccepted_inspect_only'
        print(json.dumps(dict(status='unaccepted', code=code, automatic_retry=False)), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__': main()
