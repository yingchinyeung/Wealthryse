"""Preserve per-subject successes without concealing the failed final harness check."""
import json
import control_upgrade_all_reports_v2_20261011 as a

OUT=a.ROOT/'.work/platform-upgrade-20261011-stage32/report-reconciliation-v1'

def main():
    failed=a.protected(a.OUT/'failure.json')
    readback=a.protected(a.ROOT/'.work/platform-upgrade-20261011-stage32/report-readback-v1/result.json')
    integrity=a.protected(a.ROOT/'.work/platform-upgrade-20261011-stage32/post-report-integrity-v1/result.json')
    if failed['proof']['stage']!='readback:hong-kong-china' or not readback['passed'] or not integrity['passed']:
        raise ValueError('Unexpected evidence')
    name=failed['execution'];prefix='UPGRADE_ALL_REPORTS_SUBJECT '
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=a.b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    subjects=[json.loads(r['Result']) for r in rows]
    expected={s['subject']:s for s in readback['proof']['subjects']}
    if len(subjects)!=5 or {s['subject'] for s in subjects}!=set(expected):raise ValueError('Five successful receipts required')
    for s in subjects:
        e=expected[s['subject']]
        if not s['generated'] or not s['idempotent_replay'] or s['snapshot_id']!=e['snapshot_id']:raise ValueError('Incomplete subject acceptance')
        actual={(r['report_type'],r['generation_run_id'],r['sha256']) for r in s['artifacts']}
        wanted={(r['report_type'],r['generation_run_id'],r['sha256']) for r in e['artifacts']}
        if actual!=wanted:raise ValueError('Readback differs from trial receipt')
    proof={'passed':True,'successful_generation_and_replay_receipts':5,'hash_verified_artifacts':10,
           'subjects':subjects,'original_trial_final_status':'FAILED_PRESERVED',
           'failure_after_all_five_subject_checks':True,
           'suspected_harness_cause':'MS helper uploads bypass the worker.upload_bytes tracking list; final inventory guard requires independent reconciliation.',
           'full_inventory_guard_reconciled':False,'g04_complete':False,
           'readback':readback['protected_receipt'],'integrity':integrity['protected_receipt'],
           'production_data_or_approvals_changed':False}
    if (OUT/'result.json').exists():raise ValueError('Preserve prior reconciliation')
    a.save(OUT,'result',proof)
    print(json.dumps({k:v for k,v in proof.items() if k not in ('subjects','readback','integrity')}))

if __name__=='__main__':main()
