"""Current acceptance ledger, superseding stale carried-forward gate labels."""
import json,os,subprocess,sys
from datetime import datetime,timezone
import control_upgrade_metadata_private_20261010 as c


def main():
    p=c.p
    p.main('verify')
    c.main('read')
    endpoint=p.protected(p.OUT/'verified.json')
    runtime=p.protected(c.OUT/'result.json')
    intent=p.protected(c.OUT/'intent.json')
    c.accept(runtime['proof'],endpoint['private_ip'],intent['source_sha256'])
    if runtime.get('passed') is not True or runtime.get('saved_job_unchanged') is not True:
        raise ValueError('Private metadata path not accepted')
    root=p.OUT.parents[2]
    # OUT is <root>/.work/stage27/metadata-private-path-v1.
    stage26=p.protected(root/'.work/platform-upgrade-20261010-stage26/closeout.json')
    if stage26.get('passed') is not True or stage26.get('remaining_image_families')!=0:
        raise ValueError('Website and legacy-runtime checkpoint incomplete')
    run=subprocess.run([sys.executable,'-m','unittest',
        'scripts/test_upgrade_metadata_private_path_20261010.py',
        'scripts/test_upgrade_metadata_private_probe_20261010.py',
        'scripts/test_secret_metadata_monitor_20261010.py','-q'],cwd=root,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(root/'src',root/'scripts')))},
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    if run.returncode or 'Ran 35 tests' not in run.stderr:raise ValueError('Private metadata boundary tests failed')
    p.save(p.OUT.parent,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),
        'passed':True,'endpoint_receipt':endpoint['protected_receipt'],
        'runtime_receipt':runtime['protected_receipt'],'website_receipt':stage26['protected_receipt'],
        'tests_passed':35,'new_endpoints':1,'private_ip':endpoint['private_ip'],
        'secret_values_read':0,'secret_values_changed':0,'saved_job_unchanged':True,
        'dns_linked':False,'dns_record_created':False,'public_access_changed':False,
        'permissions_changed':False,'financial_data_changed':False,'reports_changed':False,
        'completed_gates':['tracked legacy runtime replacement','MS/T model-cash deployment and focused responsive acceptance','native August presentation package equivalence'],
        'remaining_gates':['private DNS record/link completion and all-caller, platform-secret, cold-pull, rollback and negative-external acceptance before public restrictions',
            'credential ownership and actual rotation review; no fabricated expiry or rotation metadata',
            'isolated fresh monthly generation, upload failure, concurrency, recovery and governed historical replacement acceptance',
            'broader website and download acceptance',
            'genuine complete scheduled cycle and 14-day observation'],
        'gate_reconciliation':'The stage25/26 carried-forward remaining_gates labels include completed legacy-image and focused cash-display work. This ledger marks those completed; it does not rewrite their protected records.',
        'real_viewer_sign_in':'Explicitly unverified by owner choice',
        'human_alert_receipt_confirmed':False,'full_upgrade_complete':False})
    print('Private metadata path sealed; current remaining-gate ledger retained')


if __name__=='__main__':main()
