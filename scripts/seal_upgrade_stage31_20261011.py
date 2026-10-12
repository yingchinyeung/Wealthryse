"""Seal only evidenced DNS/credential/snapshot work; leave full upgrade open."""
import json,os,re,subprocess,sys
from datetime import datetime,timezone
import control_upgrade_main_dns_20261011 as c

OUT=c.ROOT/'.work/platform-upgrade-20261011-stage31'
def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve prior closeout')
    resume=c.old.protected(OUT/'resume-v1/result.json')
    plan=c.old.protected(c.OUT/'plan.json')
    private=c.old.protected(c.OUT/'private-probe-v1/result.json')
    public=c.old.protected(c.OUT/'public-probe-v1/result.json')
    rollback=c.old.protected(c.OUT/'rollback-verified.json')
    groups=c.old.protected(OUT/'credential-groups-v2/result.json')
    scope=c.old.protected(OUT/'reporting-scope-v1/result.json')
    prepared=c.old.protected(OUT/'fresh-reporting-db-v1/result.json')
    snapshots=c.old.protected(OUT/'fresh-snapshots-v1/result.json')
    integrity=c.old.protected(OUT/'fresh-snapshot-integrity-v1/result.json')
    differences=c.old.protected(OUT/'snapshot-difference-v2/result.json')
    if any(p.get('passed') is not True for p in (private,public,rollback,scope,prepared,snapshots,integrity,differences)):raise ValueError('Required accepted evidence missing')
    if not groups['all_16_database_secret_targets_classified'] or not groups['production_groups_consistent']:raise ValueError('Credential grouping incomplete')
    if c.boundary()!=plan['boundary'] or c.old.bindings(c.old.current_resources())!=resume['bindings']:raise ValueError('Live configuration drift')
    c.links(False);health=c.health()
    for zone in c.old.ZONES:
        if c.listing(c.old.zone_id(zone)+'/virtualNetworkLinks','2020-06-01'):raise ValueError('Unexpected legacy DNS link')
    tests=['scripts/test_upgrade_main_dns_20261011.py','scripts/test_upgrade_legacy_dns_20261010.py','scripts/test_upgrade_credential_groups_20261011.py',
           'scripts/test_upgrade_fresh_reporting_boundary_20261011.py','scripts/test_upgrade_g04_isolation_20261010.py','scripts/test_upgrade_snapshot_difference_20261011.py','tests.test_reporting_snapshot','tests.test_report_design_updates']
    result=subprocess.run([sys.executable,'-m','unittest',*tests,'-q'],cwd=c.ROOT,capture_output=True,text=True,timeout=90,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'})
    if result.returncode or re.findall(r'Ran (\d+) tests',result.stderr)!=['82']:raise ValueError('Regression evidence differs')
    value={'passed':True,'sealed_at':datetime.now(timezone.utc).isoformat(),'previous_checkpoint':resume['previous_checkpoint'],
        'resume':resume['protected_receipt'],'website':health,'workloads_preserved':37,
        'main_private_dns_tls_hosts':9,'main_public_rollback_dns_tls_hosts':9,
        'private_probe':private['protected_receipt'],'rollback_probe':public['protected_receipt'],
        'main_and_legacy_rehearsal_links_absent':True,'public_access_changed':False,
        'database_credentials_classified':16,'production_role_groups':11,'matched_password_aliases':4,
        'stopped_private_trial_credential_retained':True,'credential_review':groups['protected_receipt'],'credentials_changed':False,
        'all_subject_input_integrity':scope['protected_receipt'],'isolated_qa_copy':prepared['protected_receipt'],
        'fresh_snapshots_created':5,'fresh_snapshot_replay_passed':True,'snapshot_trial':snapshots['protected_receipt'],
        'integrity':integrity['protected_receipt'],'qa_tables_unchanged_after_snapshot_creation':96,'recovery_source_tables_unchanged':97,
        'snapshot_comparison':integrity['proof']['comparison_to_preexisting_frozen_inputs'],
        'snapshot_difference_classification':differences['protected_receipt'],
        'targeted_tests':82,'financial_source_data_changed':False,'production_reports_or_approvals_changed':False,'viewer_entitlements_changed':False,
        'remaining_gates':['Final private-network promotion, all-caller platform-secret refresh, uncached image pulls and external denial checks',
                           'Credential rotation recovery/reconnection tests, coordinated rotation and provider expiry review',
                           'Qualify snapshot differences, all-subject fresh artifact generation and governed regeneration',
                           'Wider website acceptance and genuine whole scheduled cycle'],
        'observation_14_day':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
        'full_upgrade_complete':False}
    c.old.save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_verified':True,'fresh_snapshots':5,'dns_round_trip_hosts':9,'tests':82,'full_upgrade_complete':False}))

if __name__=='__main__':main()
