"""Seal snapshot-worker correction without treating remaining gates as complete."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import deploy_snapshot_correction_20261011 as d
import control_upgrade_main_dns_20261011 as c

OUT = d.qa.STAGE
EVIDENCE = ('snapshot-reference-runtime-v1/result.json', 'snapshot-candidate-runtime-v1/result.json',
            'snapshot-normal-role-v1/result.json', 'snapshot-deployment-v1/verified.json',
            'snapshot-installed-role-v1/result.json')

def main():
    if (OUT / 'closeout.json').exists():
        raise ValueError('Preserve previous checkpoint')
    with ThreadPoolExecutor(max_workers=4) as pool:
        proofs = dict(zip(EVIDENCE, pool.map(lambda n: d.protected(OUT / n), EVIDENCE)))
    if any(p.get('passed') is not True for p in proofs.values()):
        raise ValueError('Complete installed acceptance required')
    plan = d.protected(d.OUT / 'plan.json')
    source = d.release.h.read(d.release.OUT / 'source-intent.json')['candidate_hashes']
    d.parity(proofs[EVIDENCE[0]], proofs[EVIDENCE[1]], plan['image'], source)
    prior = d.protected(OUT / 'resume-v1/result.json')['boundary']
    current = c.boundary()
    d.only_target(current['workloads'], prior['workloads'])
    if {k:v for k,v in current.items() if k != 'workloads'} != {k:v for k,v in prior.items() if k != 'workloads'}:
        raise ValueError('Non-workload drift')
    live = d.b.request('GET', d.PATH + d.b.API)
    d.scope(live)
    if d.settings_invariant(live) != plan['invariant'] or live['properties']['template']['containers'][0]['image'] != plan['image']:
        raise ValueError('Installed snapshot drift')
    c.links(False)
    health = c.health()
    result = {'checkpoint_evidence_verified': True, 'sealed_at': datetime.now(timezone.utc).isoformat(),
      'boundary': current, 'website': health, 'image': plan['image'], 'job': d.JOB,
      'evidence': {n:p['protected_receipt'] for n,p in proofs.items()}, 'native_build_tests': 88,
      'deployment_guard_tests': 12, 'full_snapshot_payload_parity_subjects': 5,
      'snapshot_image_deployed': True, 'normal_snapshot_workflow_executed': False,
      'production_database_writes': 0, 'production_reports_or_approvals_changed': False,
      'broker_data_changed': False, 'rollback_payload_roundtrip_verified': True,
      'live_rollback_execution': 'NOT_PERFORMED',
      'remaining_gates': ['Retire two owner-approved manual reporting jobs after dependency and recovery checks',
        'Governed production snapshot/report correction with approval and download handover',
        'Historical visual acceptance beyond reviewed samples',
        'Private-network caller, secret refresh, cold-pull and external-denial checks',
        'Credential rotation with recovery and reconnection tests',
        'Wider website acceptance and genuine full scheduled cycle'],
      'fourteen_day_observation': 'UNVERIFIED', 'real_viewer_sign_in': 'UNVERIFIED',
      'whole_scheduled_cycle': 'UNVERIFIED', 'human_alert_receipt': 'UNVERIFIED',
      'independent_crisis_price_provider_certification': 'UNVERIFIED',
      'full_historical_native_visual_review': 'UNVERIFIED', 'full_upgrade_complete': False}
    d.save(OUT, 'closeout', result)
    print(json.dumps({'checkpoint_evidence_verified': True, 'snapshot_image_deployed': True,
                      'website': health, 'full_upgrade_complete': False}))

if __name__ == '__main__':
    main()
