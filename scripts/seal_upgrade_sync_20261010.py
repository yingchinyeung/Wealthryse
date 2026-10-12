"""Seal installed security and read-only acceptance, not a normal import cycle."""
import json
from datetime import datetime, timezone
from deploy_upgrade_sync_security_20261010 import OUT, candidate, accept, PATH, API
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain


def main():
    target = OUT / 'closeout.json'
    if target.exists():
        raise ValueError('Preserve existing closeout')
    old, image = candidate()
    plan = protected(OUT / 'deployment-v1/plan.json')
    deployed = protected(OUT / 'deployment-v1/verified.json')
    installed = protected(OUT / 'installed-read-v1/result.json')
    accept(protected(OUT / 'postgres-v1/result.json'), image)
    accept(protected(OUT / 'postgres-baseline-v1/result.json'), old)
    live = request('GET', PATH + API)
    if live['properties']['template']['containers'][0]['image'] != image or settings_invariant(live) != plan['invariant']:
        raise ValueError('Installed settings drift')
    if not deployed['passed'] or not installed['passed'] or any(r['image'] != image for r in (plan, deployed, installed)):
        raise ValueError('Exact installed acceptance required')
    proof = installed['proof']
    if not proof['source_hash_verified'] or proof['application_files_unchanged'] != 452 or any(proof[k] != 0 for k in ('database_connections', 'database_writes', 'blob_writes', 'broker_calls')):
        raise ValueError('Read-only source preservation required')
    value = {
        'passed': True, 'milestone': 'Sync security runtime and installed read acceptance',
        'image': image, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'application_files_unchanged': 452, 'settings_preserved': True,
        'candidate_and_fallback_sql_checks_each': 6, 'persistent_clone_tables_unchanged': 92,
        'source_hash_verified': True, 'source_age_hours_at_probe': proof['source_age_hours'],
        'normal_freshness_gate_passed_at_probe': proof['normal_freshness_gate_passed'],
        'normal_cycle_verified': False, 'burn_in_complete': False, 'full_upgrade_complete': False,
        'fallback_limitation': plan['rollback_limitation'],
        'receipts': {'deployment': deployed['protected_receipt'], 'installed_read': installed['protected_receipt']},
    }
    receipt = retain(value, 'upgrade-sync-security-closeout')
    with target.open('x', encoding='utf-8') as handle:
        json.dump({**value, 'protected_receipt': receipt}, handle, indent=2)
    print('Sync security milestone sealed; freshness, normal cycle and full upgrade remain separate')


if __name__ == '__main__':
    main()
