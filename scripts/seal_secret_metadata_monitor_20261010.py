"""Close metadata monitoring without claiming rotation or human delivery."""
from datetime import datetime,timezone
import json,os,re,subprocess,sys
import control_secret_metadata_monitor_20261010 as c
import alert_secret_metadata_monitor_20261010 as a


def main():
    out=c.OUT.parent
    if (out/'closeout.json').exists():raise ValueError('Preserve existing stage closeout')
    plan=c.protected(c.OUT/'plan.json');c.unchanged(plan)
    identity=c.request('GET',c.IDENTITY+'?api-version=2023-01-31');c.verify_roles(identity)
    job=c.request('GET',c.JOBPATH+c.API);c.verify_job(job,identity,True)
    runtime=c.protected(c.OUT/'runtime-verified.json');schedule=c.protected(c.OUT/'schedule-verified.json')
    alert=c.protected(a.OUT/'verified.json');a.verify(c.request('GET',a.PATH))
    tests=c.protected(a.OUT/'qualification.json')
    if not all(x.get('passed') is True for x in (runtime,schedule,alert,tests)):raise ValueError('Required monitoring evidence incomplete')
    for item in plan['vaults']:
        current=c.request('GET',c.rid('Microsoft.KeyVault/vaults',item['name'])+'?api-version=2023-07-01')
        if c.fingerprint(current['properties'])!=item['configuration_sha256']:raise ValueError('Existing vault configuration changed')
    modules=['scripts/test_secret_metadata_monitor_20261010.py','scripts/test_secret_metadata_deployment_20261010.py','scripts/test_upgrade_workflow_guards_20261010.py']
    run=subprocess.run([sys.executable,'-m','unittest',*modules,'-q'],cwd=c.ROOT,env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(c.ROOT/'src',c.ROOT/'scripts'))),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf-8',timeout=60)
    counts=re.findall(r'Ran (\d+) tests',run.stderr)
    if run.returncode or counts!=['49']:raise ValueError('Final local safety tests failed')
    with (out/'guard-tests.log').open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
    source_files=['secret_metadata_monitor_20261010.py','test_secret_metadata_monitor_20261010.py','control_secret_metadata_monitor_20261010.py','alert_secret_metadata_monitor_20261010.py','test_secret_metadata_deployment_20261010.py', 'seal_secret_metadata_monitor_20261010.py']
    import hashlib
    source={name:hashlib.sha256((c.ROOT/'scripts'/name).read_bytes()).hexdigest() for name in source_files}
    summary=runtime['result'];issues={}
    for vault in summary['vaults']:
        for finding in vault['findings']:
            for reason in finding['reasons']:issues[reason]=issues.get(reason,0)+1
    prior=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage21/closeout.json')
    c.save(out,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),'monitor_job':c.JOB,'image':c.IMAGE,
        'definition_count':len(c.current_resources()),'dedicated_identity':c.IDENTITY,'metadata_vaults':list(c.monitor.VAULTS),
        'role_assignments':{'key_vault_reader':3,'acr_pull':1,'secret_value_roles':0,'database_roles':0},
        'schedule':'15 6 * * * UTC','native_self_tests':24,'kql_fault_cases':26,'final_local_safety_tests':49,
        'listed_secrets':sum(v['listed'] for v in summary['vaults']),'enabled_secrets':sum(v['enabled'] for v in summary['vaults']),
        'secrets_with_metadata_findings':summary['finding_count'],'findings_by_reason':issues,
        'runtime_receipt':runtime['protected_receipt'],'schedule_receipt':schedule['protected_receipt'],'alert_receipt':alert['protected_receipt'],
        'source_files_sha256':source,'original_vault_configurations_unchanged':True,'other_workloads_unchanged':True,
        'secret_values_read':0,'secret_values_changed':0,'expiry_changes':0,'financial_data_changed':False,'report_approvals_changed':False,
        'actual_alert_evaluation_delivery_observed':False,'human_inbox_receipt_confirmed':False,'normal_scheduled_monitor_execution_observed':False,
        'credential_rotation_and_ownership_review_complete':False,'remaining_image_families':8,'remaining_images':prior['remaining_images'],
        'remaining_gates':['remaining image qualification and credential ownership/rotation review','private-network cutover and all-caller/cold-rollback tests',
            'reporting generation/recovery acceptance','responsive UI and model-cash display acceptance','genuine scheduled cycle and 14-day observation'],
        'real_viewer_acceptance':'Explicitly unverified by owner choice','full_upgrade_complete':False})
    print(json.dumps({'sealed':True,'vaults':3,'metadata_findings':summary['finding_count'],'findings_by_reason':issues,'full_upgrade_complete':False}))


if __name__=='__main__':main()
