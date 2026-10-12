"""Seal qualified handover evidence without claiming production deployment."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import release_report_handover_20261011 as release
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage42'

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve checkpoint')
    previous=protected(ROOT/'.work/platform-upgrade-20261011-stage41/closeout.json')
    boundary=release.dns.current_boundary_function()()
    if boundary!=previous['boundary']:raise ValueError('Production boundary drift')
    release.dns.original.links(False)
    source=json.loads((release.OUT/'source-intent.json').read_bytes())
    if release.h.manifest(release.OUT/'context')!=source['files_sha256']:raise ValueError('Frozen source drift')
    native=json.loads((release.OUT/'native-build-qualified.json').read_bytes())
    scan=json.loads((release.OUT/'scan-completed.json').read_bytes())
    receipt=json.loads((release.OUT/'scan-protected-receipt.json').read_bytes())
    remote,_=restore_protected(receipt)
    if remote!=scan or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings'] or native['linux_tests']!=72 or native['proof']['candidate_hashes']!=source['candidate_hashes']:raise ValueError('Candidate qualification failed')
    qa=protected(OUT/'handover-qa-v1/result.json');schema=protected(OUT/'schema-v1/result.json')
    duplicates=protected(OUT/'duplicates-v1/result.json');blobs=protected(OUT/'duplicate-blobs-v1/result.json')
    if not all(p['passed'] for p in (qa,schema,duplicates,blobs)) or qa['image']!=scan['image'] or qa['proof']['persistent_database_changes']!=0 or qa['proof']['tables_unchanged']!=97:raise ValueError('Rehearsal incomplete')
    if [r['artifact_release_id'] for r in duplicates['proof']['duplicate_registrations']]!=[55,57] or blobs['local_report_files_created']!=0:raise ValueError('Duplicate provenance differs')
    save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':previous['protected_receipt'],'boundary':boundary,'website':release.dns.original.health(),
        'candidate_image':scan['image'],'candidate_state':'qualified_private_candidate_not_deployed',
        'candidate_files':source['candidate_hashes'],'native_tests':72,'native_evidence':scan['protected_artifact_receipts']['native-build-qualified'],
        'scan_receipt':receipt,'scan_counts':scan['assessment']['counts'],'qa_receipt':qa['protected_receipt'],
        'schema_rollback_passed':True,'approval_rollback_passed':True,'cross_scope_isolation_passed':True,'qa_tables_unchanged':97,
        'schema_preflight':schema['protected_receipt'],'production_supersession_column_missing':True,
        'duplicate_registration_receipt':duplicates['protected_receipt'],'duplicate_blob_receipt':blobs['protected_receipt'],
        'unresolved_duplicate':{'subject':'alpha-world-ms','period_end':'2026-09-30','report_type':'factsheet','older_release_id':55,'newer_release_id':57},
        'new_local_report_files_created':0,'production_reports_or_approvals_changed':False,'production_database_writes':0,
        'public_access_changed':False,'credentials_rotated':False,'saved_production_workloads_changed':False,
        'remaining_gates':['Controlled schema migration and reconciliation of the duplicate MS factsheet registration before handover website rollout']+previous['remaining_gates'],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','independent_crisis_price_provider_certification':'UNVERIFIED',
        'full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_verified':True,'native_tests':72,'private_postgres_rollback_passed':True,'candidate_deployed':False,'full_upgrade_complete':False}))

if __name__=='__main__':main()
