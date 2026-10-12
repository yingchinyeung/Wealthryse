"""Retire only the owner's exact 22 archived diagnostic jobs; no other deletes."""
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
from plan_upgrade_owner_retirement_20261010 import (OUT, ROOT, API, SOURCE, BASE, request, rid,
    settings_invariant, protected, save, load_plan, listing, bindings, assert_manual_idle)
import deploy_t_runtime_release_20261009 as runtime

APPROVED = frozenset(('ibkrv2stg-ownar2-20261006', 'ibkrv2stg-ownard-20261006',
    'ibkrv2stg-ownaud2-20261005', 'ibkrv2stg-ownaud3-20261005', 'ibkrv2stg-ownaudsql-20261005',
    'ibkrv2stg-ownaw-20261005', 'ibkrv2stg-ownaw2-20261006', 'ibkrv2stg-owncatd-20261005',
    'ibkrv2stg-ownerruntime-20261004', 'ibkrv2stg-ownertrial-catalog', 'ibkrv2stg-ownfenc2-20261005',
    'ibkrv2stg-ownfenc3-20261005', 'ibkrv2stg-ownfence-20261005', 'ibkrv2stg-ownobj-20261005',
    'ibkrv2stg-ownobjv-20261005', 'ibkrv2stg-ownsafe-20261005', 'ibkrv2stg-ownscope-20261005',
    'ibkrv2stg-ownsql-20261005', 'ibkrv2stg-ownsql-root-20261005', 'ibkrv2stg-owntls-20261005',
    'ibkrv2stg-owntlsv-20261005', 'ibkrv2stg-owntrial-20261005'))


def target(entry):
    name = entry['name']
    if name not in APPROVED or entry['id'] != rid('Microsoft.App/jobs', name): raise ValueError('Deletion outside approved exact job scope')
    return entry['id']


def validate_plan(plan, qualified):
    if len(plan['jobs']) != 22 or {j['name'] for j in plan['jobs']} != APPROVED or set(plan['targets']) != APPROVED: raise ValueError('Exact 22-job archive required')
    if plan['owner_authorization'] != 'Approve retirement after dependency checks' or qualified.get('passed') is not True or qualified['plan_receipt'] != plan['protected_receipt']: raise ValueError('Bound authorization/dependency gate required')
    for j in plan['jobs']: target(j)


def current_bindings():
    return bindings(listing(BASE + 'Microsoft.App/containerApps', '2026-07-01') + listing(BASE + 'Microsoft.App/jobs', '2026-07-01'))


def preserve_others(actual, plan):
    actual = {n: v for n, v in actual.items() if n not in APPROVED}
    if actual != plan['non_target_bindings']: raise ValueError('Non-target app/job changed; re-review before deleting')


def current_target(entry):
    path = target(entry); job = request('GET', path + API)
    if settings_invariant(job) != entry['invariant'] or job['properties']['template']['containers'][0]['image'] != SOURCE: raise ValueError('Archived target changed')
    runs = listing(path + '/executions', '2026-07-01'); assert_manual_idle(job, runs)
    if {r['name'] for r in runs} != {r['name'] for r in entry['executions']}: raise ValueError('Execution history changed; archive new evidence first')
    return job


def restore_body(entry):
    target(entry); job = entry['definition']; p = job['properties']
    if job['identity']['type'] != 'UserAssigned': raise ValueError('Unexpected identity; restoration not qualified')
    return {'location': job['location'], 'tags': job.get('tags', {}),
        'identity': {'type': 'UserAssigned', 'userAssignedIdentities': {i: {} for i in job['identity']['userAssignedIdentities']}},
        'properties': {k: p[k] for k in ('environmentId', 'workloadProfileName', 'configuration', 'template') if k in p and p[k] is not None}}


def main(stage):
    plan = load_plan(); qualified = protected(OUT / 'dependency-qualified.json'); validate_plan(plan, qualified)
    controller_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage == 'prepare':
        preserve_others(current_bindings(), plan)
        for entry in plan['jobs']: current_target(entry)
        save(OUT, 'retirement-ready', {'passed': True, 'plan_receipt': plan['protected_receipt'],
            'dependency_receipt': qualified['protected_receipt'], 'controller_sha256': controller_hash,
            'targets': sorted(APPROVED), 'restore_bodies': {j['name']: restore_body(j) for j in plan['jobs']},
            'production_website_or_scheduled_job_deletion': False, 'restore_warning': 'Definitions are recoverable; old Azure execution IDs cannot be recreated. Archived metadata and existing Log Analytics history remain available.'})
        print('Exact retirement plan and recovery definitions archived; no deletion'); return
    ready = protected(OUT / 'retirement-ready.json')
    if ready['controller_sha256'] != controller_hash or ready['plan_receipt'] != plan['protected_receipt']: raise ValueError('Protected retirement controller drift')
    if stage == 'apply':
        runtime.safe_window(); preserve_others(current_bindings(), plan)
        for entry in plan['jobs']: current_target(entry)
        if (OUT / 'delete-batch-intent.json').exists(): raise ValueError('Reconcile prior batch before any further deletion')
        save(OUT, 'delete-batch-intent', {'started_at': datetime.now(timezone.utc).isoformat(), 'ready_receipt': ready['protected_receipt'],
            'resource_ids': [target(j) for j in plan['jobs']], 'identities_databases_reports_schedules_unchanged': True})
        for entry in plan['jobs']:
            current_target(entry)
            folder = OUT / entry['name']
            save(folder, 'delete-intent', {'resource_id': target(entry), 'baseline_receipt': entry['baseline_receipt'], 'ready_receipt': ready['protected_receipt']})
            ack = request('DELETE', target(entry) + API)
            save(folder, 'delete-ack', {'resource_id': target(entry), 'request_returned_success': True,
                'response_sha256': hashlib.sha256(json.dumps(ack, sort_keys=True).encode()).hexdigest()})
            print(json.dumps({'job': entry['name'], 'delete_submitted': True}), flush=True)
        return
    if stage != 'verify': raise ValueError('Unknown retirement stage')
    intent = protected(OUT / 'delete-batch-intent.json')
    if intent['ready_receipt'] != ready['protected_receipt']: raise ValueError('Batch binding differs')
    present = []
    for entry in plan['jobs']:
        if request('GET', target(entry) + API, missing=True) is not None: present.append(entry['name'])
        else:
            folder = OUT / entry['name']
            # A missing acknowledgement can be reconciled only against its exact
            # protected intent. Never reissue a deletion after an unknown outcome.
            p = protected(folder / 'delete-intent.json')
            if p['resource_id'] != target(entry): raise ValueError('Deletion intent differs')
    preserve_others(current_bindings(), plan)
    if present: print(json.dumps({'still_present': present, 'complete': False})); return
    if not (OUT / 'verified.json').exists():
        save(OUT, 'verified', {'passed': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'retired_jobs': sorted(APPROVED), 'retired_count': 22, 'archived_executions': sum(len(j['executions']) for j in plan['jobs']),
            'all_other_app_job_settings_and_images_preserved': True, 'plan_receipt': plan['protected_receipt'],
            'restore_receipt': ready['protected_receipt'], 'identities_databases_reports_deleted': False, 'full_upgrade_complete': False})
    print('22 approved diagnostic definitions retired; all other apps/jobs unchanged')


if __name__ == '__main__': main(sys.argv[1])
