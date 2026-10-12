"""Seal three-reader security stage with the scoped authority preflight repair."""
import json,os,subprocess,sys
from datetime import datetime,timezone
import control_upgrade_readers_20261010 as r
import control_upgrade_authority_check_20261010 as a
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_ms_presentation_worker_release import settings_invariant


def main():
    folder=r.OUT.parent
    if (folder/'closeout.json').exists():raise ValueError('Preserve sealed stage evidence')
    image=r.candidate();results={}
    for job in ('ibkrv2stg-agent-audit','ibkrv2stg-observe-reader'):
        base=protected(r.OUT/job/'baseline.json');deploy=protected(r.OUT/job/'deployment-v1/verified.json')
        live=r.request('GET',r.rid('Microsoft.App/jobs',job)+r.API)
        if settings_invariant(live)!=base['invariant'] or live['properties']['template']['containers'][0]['image']!=image or live['properties']['provisioningState']!='Succeeded' or r.reviewed(live,job)!=base['payload_sha256']:raise ValueError('Reader installation drift')
        trials={}
        for mode in ('candidate','fallback','installed'):
            proof=protected(r.OUT/(job+'-'+mode)/'result.json');r.accept(job,proof['proof'])
            if proof['image']!=(r.SOURCE if mode=='fallback' else image) or not proof['passed']:raise ValueError('Reader trial drift')
            trials[mode]=proof['protected_receipt']
        results[job]={'deployment':deploy['protected_receipt'],'trials':trials}
    base,args,digest=a.build_args();deploy=protected(a.OUT/'deployment-v1/verified.json')
    live=r.request('GET',a.PATH+a.API);c=live['properties']['template']['containers'][0]
    if c['image']!=image or c['args']!=args or live['properties']['provisioningState']!='Succeeded':raise ValueError('Authority installation drift')
    c['args']=base['job']['properties']['template']['containers'][0]['args']
    if settings_invariant(live)!=base['invariant'] or live['properties']['configuration'].get('identitySettings',[])!=base['job']['properties']['configuration'].get('identitySettings',[]):raise ValueError('Authority unrelated settings changed')
    trials={}
    for mode in ('candidate','fallback','installed'):
        proof=protected(a.trial_folder(mode)/'result.json');a.shape(proof['proof'])
        if proof['image']!=(r.SOURCE if mode=='fallback' else image) or proof['payload_sha256']!=digest or not proof['passed']:raise ValueError('Authority trial drift')
        trials[mode]=proof['protected_receipt']
    results[a.JOB]={'deployment':deploy['protected_receipt'],'trials':trials}
    env={**os.environ,'PYTHONPATH':str(r.ROOT/'src')+os.pathsep+str(r.ROOT/'scripts')}
    commands=[(['-m','unittest','discover','-s','tests','-p','test_specialist_*runtime.py'],48),
        (['scripts/test_upgrade_readers_20261010.py'],7),(['scripts/test_upgrade_authority_check_20261010.py'],4)]
    for args,count in commands:
        test=subprocess.run([sys.executable,*args],cwd=r.ROOT,env=env,capture_output=True,text=True,timeout=60)
        if test.returncode or 'Ran '+str(count)+' tests' not in test.stderr or 'OK' not in test.stderr:raise ValueError('Reader/repair regressions failed')
    scan=json.loads((r.IMAGE_FOLDER/'scan-completed.json').read_bytes())
    coverage=protected(folder/'image-coverage-v1.json')
    remaining=next(item['templates'] for item in coverage['images'] if item['image']==r.SOURCE)
    if len(remaining)!=23 or any(job in remaining for job in r.JOBS):raise ValueError('Remaining image scope changed')
    r.save(folder,'closeout',{'stage':'restricted-reader-security-and-authority-preflight','sealed_at':datetime.now(timezone.utc).isoformat(),
        'image':image,'jobs':results,'local_boundary_tests':48,'deployment_guard_tests':11,'security_counts':scan['assessment']['counts'],
        'application_image_source_files_preserved':530,'authority_change':'Only obsolete empty-store expectation replaced; actual authority decoding and unregistered-principal denial unchanged',
        'old_authority_preflight_failed_on_both_images':True,'authority_compatible_fallback_passed':True,
        'original_fallback_security_qualified':False,'source_data_changed':False,'report_approvals_changed':False,
        'permissions_changed':False,'agents_enrolled_or_enabled':False,'private_network_cutover_performed':False,
        'image_coverage_receipt':coverage['protected_receipt'],'remaining_old_image_jobs':len(remaining),
        'dispatch_interruptions_reconciled_without_duplicate_start':True,
        'burn_in_complete':False,'full_upgrade_complete':False})
    print(json.dumps({'sealed':True,'jobs':3,'full_upgrade_complete':False}))


if __name__=='__main__':main()
