"""Record the production metadata correction and preserve outstanding gates."""
import json
from datetime import datetime,timezone
from pathlib import Path
import release_report_handover_20261011 as release
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage43'

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve checkpoint')
    previous=protected(ROOT/'.work/platform-upgrade-20261011-stage42/closeout.json')
    current=release.dns.current_boundary_function()()
    if current!=previous['boundary']:raise ValueError('Production application/configuration drift')
    rehearsal=protected(OUT/'ms-duplicate-rehearse-v1/result.json')
    applied=protected(OUT/'ms-duplicate-apply-v1/result.json')
    checked=protected(OUT/'ms-duplicate-postcheck-v1/result.json')
    cleanup=protected(ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-pdf-review-v1/cleanup.json')
    if not all(p['passed'] for p in (rehearsal,applied,checked)) or not rehearsal['proof']['rehearsal_rolled_back']:raise ValueError('Correction evidence incomplete')
    if applied['proof']['registration_rows_changed']!=1 or applied['proof']['audit_event_id']!=90 or applied['proof']['approval_decisions_changed']!=0 or applied['proof']['source_files_deleted']!=0:raise ValueError('Unexpected mutation scope')
    if checked['proof']['duplicate_active_release_groups']!=0 or checked['proof']['website_catalogue_factsheet_release']!=57 or checked['proof']['obsolete_release_withdrawn']!=55:raise ValueError('Installed catalogue not verified')
    if not cleanup['completed'] or cleanup['deleted_temporary_page_images']!=4:raise ValueError('Cleanup incomplete')
    release.dns.original.links(False)
    save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':previous['protected_receipt'],'boundary':current,'website':release.dns.original.health(),
        'rehearsal_receipt':rehearsal['protected_receipt'],'correction_receipt':applied['protected_receipt'],
        'independent_website_role_receipt':checked['protected_receipt'],'cleanup_receipt':cleanup['protected_receipt'],
        'obsolete_ms_factsheet_registration_withdrawn':55,'retained_approved_ms_factsheet_registration':57,
        'duplicate_active_release_groups':0,'report_registration_rows_changed':1,'audit_events_added':1,
        'audit_event_id':90,'approval_decisions_changed':0,'reports_deleted':0,'financial_inputs_changed':False,
        'candidate_image':previous['candidate_image'],'handover_candidate_deployed':False,'production_schema_changed':False,
        'public_access_changed':False,'credentials_rotated':False,'saved_production_workloads_changed':False,
        'remaining_gates':['Controlled schema migration and website handover candidate rollout']+previous['remaining_gates'][1:],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','independent_crisis_price_provider_certification':'UNVERIFIED',
        'full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_verified':True,'duplicate_corrected':True,'duplicate_active_release_groups':0,'full_upgrade_complete':False}))

if __name__=='__main__':main()
