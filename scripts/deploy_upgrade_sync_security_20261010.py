"""Image-only sync security release; broker/source logic and settings frozen."""
import copy
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from control_upgrade_sync_postgres_20261010 import OUT, candidate, JOB, PATH, API
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
from run_specialist_gateway_read_verification import retain
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
FOLDER = OUT / 'deployment-v1'
CHECKS = {'first_import_five_cash_rows', 'unchanged_snapshot_idempotency', 'unapproved_extras_rejected_and_financial_rows_rolled_back', 'allowlisted_recovery_rows_retained_and_disclosed', 'approved_replay_idempotency', 'unknown_recovery_rejected_and_rolled_back'}


def accept(proof, image):
    p = proof.get('proof', {})
    if not proof.get('passed') or proof.get('image') != image or not proof.get('job_settings_preserved') or not p.get('passed'):
        raise ValueError('Exact passing runtime rehearsal required')
    if set(p.get('checks', [])) != CHECKS or len(p.get('checks', [])) != 6:
        raise ValueError('All import/idempotency/rollback checks required')
    if p.get('public_tables_checked') != 92 or p.get('public_table_differences') != [] or not p.get('temporary_objects_dropped_on_disconnect'):
        raise ValueError('Full persistent-clone preservation required')
    if any(p.get(k) != 0 for k in ('production_connections', 'production_writes', 'persistent_clone_writes', 'broker_calls')):
        raise ValueError('No production/source access permitted in rehearsal')


def save(name, value):
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': retain(value, 'upgrade-sync-security-' + name)}, f, indent=2)


def main(stage):
    old, image = candidate()
    proof = protected(OUT / 'postgres-v1/result.json'); accept(proof, image)
    fallback = protected(OUT / 'postgres-baseline-v1/result.json'); accept(fallback, old)
    native = json.loads((OUT / 'native-build-qualified.json').read_bytes())
    a, b = native['proofs']
    if a['source_manifest_sha256'] != b['source_manifest_sha256'] or a['source_files'] != b['source_files'] or a['source_files'] != 452 or a['tests'] != b['tests'] or a['skipped'] != b['skipped']:
        raise ValueError('Unchanged native application and fixture coverage required')
    live = request('GET', PATH + API); actual = live['properties']['template']['containers'][0]['image']
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage == 'plan':
        if FOLDER.exists() or actual != old: raise ValueError('Existing plan or changed baseline')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets', [])): raise ValueError('Never archive inline secret values')
        body = patch(live, image); trial = copy.deepcopy(live); trial['properties']['template'] = body['properties']['template']
        if settings_invariant(trial) != settings_invariant(live): raise ValueError('Non-image mutation rejected')
        FOLDER.mkdir(); save('plan', {'before_image': old, 'image': image, 'before': live, 'invariant': settings_invariant(live), 'apply_body': body, 'rollback_body': patch(live, old), 'controller_sha256': digest, 'candidate_replay': proof['protected_receipt'], 'fallback_replay': fallback['protected_receipt'], 'source_files_unchanged': 452, 'scope': 'Sync OS/Python security runtime only; application bytes, Grok correction, credentials, import allowlist, 12-hour freshness rule, schedule, identity and all other settings unchanged.', 'rollback_limitation': 'Previous exact image passed the same isolated PostgreSQL tests, but retains 51 high and three unknown scan findings. No security waiver or passing-security claim for fallback.', 'normal_cycle_verified': False, 'full_upgrade_complete': False})
        print('Protected sync image-only deployment and exact fallback prepared'); return
    plan = protected(FOLDER / 'plan.json')
    if digest != plan['controller_sha256'] or settings_invariant(live) != plan['invariant'] or image != plan['image']:
        raise ValueError('Frozen deployment boundary drift')
    if stage in ('apply', 'restore'):
        runtime.safe_window(); runtime.idle(JOB)
        if actual != (old if stage == 'apply' else image) or (FOLDER / (stage + '-intent.json')).exists():
            raise ValueError('Reconcile existing mutation; never repeat it')
        save(stage + '-intent', {'started_at': datetime.now(timezone.utc).isoformat(), 'plan': plan['protected_receipt']})
        ack = request('PATCH', PATH + API, data=plan['apply_body'] if stage == 'apply' else plan['rollback_body'])
        save(stage + '-ack', {'state': ack.get('properties', {}).get('provisioningState')})
        print('Submitted one sync image-only update'); return
    if stage != 'verify': raise ValueError('Unknown step')
    if actual != image or live['properties']['provisioningState'] != 'Succeeded':
        print('Sync image update not yet verified'); return
    if not (FOLDER / 'verified.json').exists():
        save('verified', {'passed': True, 'image': image, 'all_other_settings_preserved': True, 'source_files_unchanged': 452, 'normal_cycle_verified': False, 'full_upgrade_complete': False})
    print('Installed sync image verified; normal source freshness/cycle acceptance remains separate')


if __name__ == '__main__': main(sys.argv[1])
