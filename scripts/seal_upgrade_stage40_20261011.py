"""Seal review progress without promoting screening to visual or release approval."""
import json
from datetime import datetime,timezone
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage40'

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve prior checkpoint')
    previous=protected(ROOT/'.work/platform-upgrade-20261011-stage39/closeout.json')
    screen=protected(OUT/'historical-review-v1/screening-qualified.json')
    recovery=protected(OUT/'private-recovery-refresh-v1/result.json')
    if not screen['passed'] or screen['files']!=210 or not recovery['passed']:
        raise ValueError('Completed bounded evidence required')
    if screen['full_visual_review']!='UNVERIFIED' or recovery['public_access_cutover_authorized_by_this_proof']:
        raise ValueError('Do not promote limited evidence to full acceptance')
    save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':previous['protected_receipt'],'boundary':previous['boundary'],
        'website':recovery['website'],'screening_receipt':screen['protected_receipt'],
        'private_recovery_receipt':recovery['protected_receipt'],
        'private_review_files':210,'download_cleanup_required':True,'download_cleanup_complete':False,
        'download_retention_reason':'Visual review still in progress; native PowerPoint automation permission pending.',
        'review_downloads_delete_after_use_owner_requested':True,
        'production_reports_or_approvals_changed':False,'production_database_writes':0,
        'public_access_changed':False,'credentials_rotated':False,
        'remaining_gates':previous['remaining_gates'],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
        'whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
        'independent_crisis_price_provider_certification':'UNVERIFIED',
        'full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_verified':True,'private_review_files':210,
                      'cleanup':'pending review completion','full_upgrade_complete':False}))

if __name__=='__main__':main()
