"""Stage23: source-preserving image-only changes to two manual Python jobs.

Reuse sealed generic gates without modifying their files. No normal report,
registration, release or financial task is run by qualification.
"""
from datetime import datetime,timezone
import copy,hashlib,json,os,re,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
import release_upgrade_workflow_security_20261010 as release
import control_upgrade_workflow_runtime_20261010 as control
import deploy_upgrade_administration_security_20261010 as deploy
import scan_ms_presentation_worker_candidate as scanner
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings
from ibkr_etl.container_scan import assess_container_scan

ROOT=release.ROOT
OUT=ROOT/'.work/platform-upgrade-20261010-stage23'
TARGETS={
    'snapshot':{'job':'ibkrv2stg-report-snapshot','digest':'43e4f93e80e5521de275bd2877fb103e4157c46719a26958edb9ddd8a6709987',
        'entry':'freeze_month_end_snapshot.py','tests':12,'test_files':['scripts/test_upgrade_snapshot_contract_20261010.py'],
        'missing_environment_error':'The reporting-snapshot job may run only in staging'},
    'registration':{'job':'ibkrv2stg-review-registration','digest':'3193e969cf3411986322ad403178b1a6b5481b793c97b6955cde941527bdfcce',
        'entry':'register_historical_review_batch.py','tests':20,'test_files':['scripts/test_upgrade_registration_contract_20261010.py'],
        'missing_environment_error':'Historical review registration may run only in staging'}}


def configure():
    release.OUT=control.OUT=OUT;release.TARGETS=control.TARGETS=TARGETS
    deploy.c=SimpleNamespace(**{name:getattr(control,name) for name in
        ('OUT','API','candidate','accept','protected','request','rid','settings_invariant','save','runtime')},
        TARGETS={name:(spec['job'],spec['entry'],spec['digest']) for name,spec in TARGETS.items()})


def baseline():
    import control_secret_metadata_monitor_20261010 as monitor
    previous=control.protected(ROOT/'.work/platform-upgrade-20261010-stage22/closeout.json')
    monitor.unchanged(control.protected(monitor.OUT/'plan.json'))
    monitor.verify_job(monitor.request('GET',monitor.JOBPATH+monitor.API),
        monitor.request('GET',monitor.IDENTITY+'?api-version=2023-01-31'),True)
    resources=current_resources()
    control.save(OUT,'baseline',{'checked_at':datetime.now(timezone.utc).isoformat(),'bindings':bindings(resources),
        'definitions':len(resources),'previous_receipt':previous['protected_receipt'],'allowed_jobs':sorted(s['job'] for s in TARGETS.values()),
        'policy':'Python-only two manual workflows. Preserve installed application/packages. No rendering, releases or normal workflow execution.'})
    print('Protected all 37 live definitions and exact two-job scope')


def scan_unchanged(folder):
    target=folder.name.removesuffix('-security-v1')
    if target not in TARGETS:raise ValueError('Outside reviewed Python workflow scope')
    source=json.loads((folder/'source-intent.json').read_bytes())
    if release.snapshot(target)!=source['before_jobs']:raise ValueError('Installed workflow drift')
    # Build policy established before scan, not inferred from absent findings.
    # These two installed images contain Python and OS packages, no JS runtime.
    docker=(folder/'context/Dockerfile').read_text(encoding='utf-8')
    if 'FROM scratch' not in docker or 'COPY --from=builder /runtime/ /' not in docker or 'COPY --from=installed --chown=10001:10001 /app/ /app/' not in docker:
        raise ValueError('Unexpected native runtime recipe')


def seal():
    previous=control.protected(ROOT/'.work/platform-upgrade-20261010-stage22/closeout.json')
    base=control.protected(OUT/'baseline.json');expected=copy.deepcopy(base['bindings']);upgrades=[]
    for target,spec in TARGETS.items():
        image,source,proofs=control.candidate(target);folder=OUT/(target+'-runtime-v1')
        deployment=control.protected(folder/'deployment-v1/verified.json')
        if deployment.get('passed') is not True or deployment['image']!=image or not deployment['all_nonimage_settings_preserved']:raise ValueError('Deployment incomplete')
        trials={}
        for mode in ('candidate','fallback','installed'):
            result=control.protected(folder/mode/'result.json');wanted=source['base_image'] if mode=='fallback' else image
            deploy.validate_trial(result,wanted,proofs['baseline' if mode=='fallback' else 'candidate']);trials[mode]=result['protected_receipt']
        scan=json.loads((OUT/(target+'-security-v1/scan-completed.json')).read_bytes())
        upgrades.append({'job':spec['job'],'image':image,'old_image':source['base_image'],'native_proofs':proofs,
            'scan_counts':scan['assessment']['counts'],'runtime_receipts':trials,'deployment_receipt':deployment['protected_receipt']})
        if expected[spec['job']]['images']!=[source['base_image']]:raise ValueError('Baseline differs')
        expected[spec['job']]['images']=[image]
    if bindings(current_resources())!=expected:raise ValueError('Unrelated workload drift')
    remaining=[r for r in previous['remaining_images'] if not set(r['jobs']).intersection(base['allowed_jobs'])]
    if len(remaining)!=6:raise ValueError('Unexpected remaining count')
    guards=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_report_admin_guards_20261010.py','-q'],cwd=ROOT,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(ROOT/'src',ROOT/'scripts'))),'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    if guards.returncode or re.findall(r'Ran (\d+) tests',guards.stderr)!=['18']:raise ValueError('Final deployment guards failed')
    with (OUT/'guard-tests.log').open('x',encoding='utf-8') as f:f.write(guards.stdout+guards.stderr)
    control.save(OUT,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),'upgrades':upgrades,'definitions':len(expected),
        'remaining_image_families':len(remaining),'remaining_images':remaining,'all_nonimage_and_unrelated_workloads_preserved':True,
        'normal_workflows_executed':False,'financial_data_changed':False,'reports_and_approvals_changed':False,
        'fallback_security_qualified':False,'final_guard_tests':18,'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'remaining_gates':previous['remaining_gates'],
        'real_viewer_acceptance':previous['real_viewer_acceptance'],'full_upgrade_complete':False})
    print('Two report-administration security upgrades sealed; six image families remain')


def predeployment_bindings():
    """Detect other deployers before any persistent image change."""
    expected=copy.deepcopy(control.protected(OUT/'baseline.json')['bindings'])
    current=bindings(current_resources())
    if set(current)!=set(expected):raise ValueError('Workload inventory changed')
    for target,spec in TARGETS.items():
        name=spec['job'];source,native,output=scanner.verify(OUT/(target+'-security-v1'))
        accepted=output['registry']+'/'+output['repository']+'@'+output['digest']
        if current[name]['images'] not in ([source['base_image']],[accepted]):raise ValueError('Unreviewed image appeared')
        expected[name]['images']=current[name]['images']
    if current!=expected:raise ValueError('Unrelated image or any non-image configuration changed')


def main(action,*args):
    configure()
    if action=='freeze':return baseline()
    if action=='seal':return seal()
    if action=='release':return release.main(*args)
    if action=='runtime':return control.main(*args)
    if action=='deploy':
        if len(args)==2 and args[1] in ('plan','apply'):predeployment_bindings()
        return deploy.main(*args)
    if action=='scan':
        target,=args
        if target not in TARGETS:raise ValueError('Unknown candidate')
        scanner.jobs_unchanged=scan_unchanged
        scanner.assess_container_scan=lambda **kwargs:assess_container_scan(**kwargs,required_types=frozenset(('debian','python-pkg')))
        sys.argv=[sys.argv[0],'--folder',str(OUT/(target+'-security-v1'))]
        return scanner.main()
    raise ValueError('Unknown action')


if __name__=='__main__':main(*sys.argv[1:])
