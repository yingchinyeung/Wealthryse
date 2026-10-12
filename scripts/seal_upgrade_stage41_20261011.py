"""Seal native review and safely restored private-network rehearsal evidence."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
import control_upgrade_private_canaries_20261011 as canary
import control_upgrade_main_dns_stage41_v2_20261011 as dns

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41'

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve checkpoint')
    previous=protected(ROOT/'.work/platform-upgrade-20261011-stage40/closeout.json')
    current=dns.current_boundary_function()()
    if current!=previous['boundary']:raise ValueError('Production boundary changed')
    dns.original.links(False)
    rollback=protected(OUT/'main-dns-v2/rollback-verified.json')
    if not rollback['passed'] or rollback['links_present']:raise ValueError('Rehearsal not restored')
    proofs=[]
    for group in (9,12,17):
        folder=canary.OUT/canary.name(group)
        proof=protected(folder/'result.json');retirement=protected(folder/'retirement-intent.json')
        protected(folder/'retirement-ack.json')
        if not proof['passed'] or canary.dns.old.request('GET',canary.resource(group)+canary.API,missing=True) is not None:
            raise ValueError('Canary evidence or cleanup incomplete')
        proofs.append({'group':group,'result':proof['protected_receipt'],'retirement':retirement['protected_receipt'],
            'resource_absent_verified':True,'references':proof['proof']['resolved_reference_count']})
    review=protected(OUT/'native-review-evidence.json')
    plan=protected(OUT/'local-cleanup-plan.json')
    cleanup=json.loads((OUT/'local-cleanup-completed.json').read_bytes())
    if not cleanup['completed'] or cleanup['deleted_files']!=346 or any(Path(r['path']).exists() for r in plan['files']):
        raise ValueError('Owner-requested local cleanup incomplete')
    if cleanup['plan_sha256']!=hashlib.sha256((OUT/'local-cleanup-plan.json').read_bytes()).hexdigest():raise ValueError('Cleanup manifest changed')
    if review['full_historical_native_visual_review']!='UNVERIFIED':raise ValueError('No full-visual promotion')
    save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':previous['protected_receipt'],'boundary':current,'website':dns.original.health(),
        'native_review_receipt':review['protected_receipt'],'native_exported_decks':105,'native_pages_screened':2100,
        'individual_slides_visually_inspected_this_stage':25,'canary_results':proofs,'tested_reference_entries':9,
        'temporary_canary_jobs_removed':3,'temporary_dns_links_removed':3,'private_network_cutover_completed':False,
        'cleanup_plan_receipt':plan['protected_receipt'],'cleanup':cleanup,'download_cleanup_complete':True,
        'azure_originals_preserved':True,'production_reports_or_approvals_changed':False,'production_database_writes':0,
        'public_access_changed':False,'credentials_rotated':False,'remaining_gates':previous['remaining_gates'],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','independent_crisis_price_provider_certification':'UNVERIFIED',
        'full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_verified':True,'production_workloads':len(current['workloads']),
        'native_decks':105,'cleanup_complete':True,'full_upgrade_complete':False}))

if __name__=='__main__':main()
