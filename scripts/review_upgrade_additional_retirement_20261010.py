"""Bind reviewed historical/local references to the seven-job retirement plan."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
import retire_upgrade_additional_diagnostics_20261010 as r

HISTORICAL={
 'scripts/deploy_owner_task_execution_fence_trial.py',
 'scripts/deploy_owner_task_model_service_audience_trial.py',
 'scripts/owner_task_execution_fence_authority_candidate.py',
 'scripts/owner_task_execution_fence_tls_candidate.py',
 'scripts/owner_task_model_isolation_trial.py',
 'scripts/owner_task_model_service_audience_trial.py',
 'scripts/qualify_owner_task_catalog_canary.py',
 'scripts/qualify_owner_task_execution_fence.py',
 'scripts/read_owner_task_catalog_logs.py',
 'scripts/verify_owner_task_catalog_canary_readback.py',
 'scripts/search_alpha_world_ms_azure_sources.py',
}
UPGRADE={
 'scripts/control_upgrade_specialist_private_20261010.py',
 'scripts/qualify_upgrade_owner_retirement_20261010.py',
 'scripts/retire_upgrade_additional_diagnostics_20261010.py',
 'scripts/scan_upgrade_special_references_20261010.py',
 'scripts/test_upgrade_additional_retirement_20261010.py',
 'scripts/test_upgrade_stage19_scan_guards_20261010.py',
}
HELPER='src/ibkr_etl/owner_codex_catalog_transport.py'


def main():
    if (r.OUT/'local-reference-review.json').exists():raise ValueError('Preserve earlier review')
    plan=r.protected(r.OUT/'plan.json')
    if not plan['plan_passed'] or plan['dependencies']:raise ValueError('Unresolved live callers')
    classified=[]
    for item in plan['local_references']:
        name=Path(item['path']).as_posix();p=r.ROOT/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Reviewed reference changed')
        if name in HISTORICAL:kind='Historical explicitly-invoked diagnostic controller or readback; frozen evidence retained, not an active scheduled caller'
        elif name in UPGRADE:kind='Bounded upgrade audit/test tooling; retired targets must no longer be used for future probes'
        elif name==HELPER:kind='Inert canary target-registration/encoding module; no import from other application modules found'
        else:raise ValueError('Unreviewed local reference: '+name)
        classified.append({**item,'classification':kind})
    importers=[]
    for folder in (r.ROOT/'src/ibkr_etl',r.ROOT/'.work/platform-upgrade-20261009-stage7/runtime/src/ibkr_etl'):
        for p in folder.glob('*.py'):
            if p.name=='owner_codex_catalog_transport.py':continue
            if 'owner_codex_catalog_transport' in p.read_text(encoding='utf-8',errors='replace'):
                importers.append(str(p.relative_to(r.ROOT)))
    if importers:raise ValueError('Application importer needs explicit review')
    tests=('test_owner_codex_isolated_target.py','test_owner_codex_catalog_transport.py','test_owner_task_catalog_canary.py')
    count=0;logs=[]
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(r.ROOT/'src'),str(r.ROOT/'scripts'))),'PYTHONDONTWRITEBYTECODE':'1'}
    for test in tests:
        done=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',test,'-v'],cwd=r.ROOT,
            env=env,capture_output=True,text=True,timeout=120)
        log=done.stdout+done.stderr
        if done.returncode:raise ValueError('Historical canary contract test failed')
        count+=int(re.findall(r'Ran (\d+) tests',log)[-1]);logs.append(log)
    if count!=56:raise ValueError('Expected 56 preserved contract tests')
    done=subprocess.run([sys.executable,'scripts/test_upgrade_additional_retirement_20261010.py'],cwd=r.ROOT,
        env=env,capture_output=True,text=True,timeout=60);guard=done.stdout+done.stderr
    if done.returncode or 'Ran 8 tests' not in guard or '\nOK' not in guard:raise ValueError('Retirement guard failure')
    r.save(r.OUT,'local-reference-review',{'passed':True,'plan_receipt':plan['protected_receipt'],
        'classified_local_references':classified,'application_importers':importers,'preserved_contract_tests':count,
        'retirement_guard_tests':8,'test_log':'\n'.join(logs),'guard_log':guard,
        'scope':'Owner authorized exactly seven historical diagnostics. No website/scheduled/reporting/onboarding caller found in inspected resources/source; no external concurrent operator reported.',
        'restore_limit':'Definitions recoverable; original execution IDs cannot be recreated. Existing audit logs and protected execution metadata retained.'})
    print(json.dumps({'passed':True,'reviewed_references':len(classified),'contract_tests':count,'guard_tests':8}))


if __name__=='__main__':main()
