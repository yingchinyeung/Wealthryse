"""Freeze all runtime bindings, then close only the two verified replacements."""
from datetime import datetime,timezone
import json,sys
import control_upgrade_workflow_runtime_20261010 as c
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings,preserve_others
from audit_upgrade_image_coverage_20261009 import inventory


def main(stage):
    if stage=='freeze':
        previous=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage20/closeout.json')
        plan=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage20/diagnostic-retirement-v1/plan.json')
        resources=current_resources();preserve_others(resources,plan)
        c.save(c.OUT,'baseline',{'checked_at':datetime.now(timezone.utc).isoformat(),'bindings':bindings(resources),
            'prior_closeout_receipt':previous['protected_receipt'],'definitions':len(resources),
            'allowed_jobs':sorted(x['job'] for x in c.TARGETS.values())})
        print('All 36 runtime definitions match sealed prior checkpoint');return
    if stage!='seal':raise ValueError('Unknown stage')
    base=c.protected(c.OUT/'baseline.json');previous=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage20/closeout.json')
    expected=base['bindings'];upgrades=[]
    for target,spec in c.TARGETS.items():
        image,source,proofs=c.candidate(target);folder=c.OUT/(target+'-runtime-v1')
        deployment=c.protected(folder/'deployment-v1/verified.json')
        if deployment.get('passed') is not True or deployment['image']!=image or not deployment['all_nonimage_settings_preserved']:raise ValueError('Unverified deployment')
        trials={}
        for mode in ('candidate','fallback','installed'):
            result=c.protected(folder/mode/'result.json');wanted=source['base_image'] if mode=='fallback' else image
            if result.get('passed') is not True or result['image']!=wanted or not result['settings_preserved']:raise ValueError('Unverified runtime')
            c.accept(result['proof'],proofs['baseline' if mode=='fallback' else 'candidate']);trials[mode]=result['protected_receipt']
        scan=json.loads((c.OUT/(target+'-security-v1/scan-completed.json')).read_bytes())
        upgrades.append({'job':spec['job'],'old_image':source['base_image'],'image':image,'native_proofs':proofs,
            'scan_counts':scan['assessment']['counts'],'runtime_receipts':trials,'deployment_receipt':deployment['protected_receipt']})
        # bindings() is the prior audited non-image-settings map, not raw definitions.
        entry=expected[spec['job']]
        if entry['images']!=[source['base_image']]:raise ValueError('Baseline image differs')
        entry['images']=[image]
    if bindings(current_resources())!=expected:raise ValueError('Unrelated runtime settings/image drift')
    rows=inventory();remaining=[x for x in previous['remaining_images'] if not set(x['jobs']).intersection(base['allowed_jobs'])]
    if len(rows)!=36 or len(remaining)!=8:raise ValueError('Unexpected coverage totals')
    c.save(c.OUT,'closeout',{'sealed_at':datetime.now(timezone.utc).isoformat(),'upgrades':upgrades,
        'all_other_app_job_settings_and_images_preserved':True,'definitions':len(rows),'remaining_image_families':len(remaining),
        'remaining_images':remaining,'remaining_gates':previous['remaining_gates'],'normal_jobs_started':False,
        'financial_rows_read':0,'financial_calculation_code_changed':False,'report_templates_changed':False,
        'report_approvals_changed':False,'authentication_changed':False,'full_upgrade_complete':False,
        'real_viewer_acceptance':previous['real_viewer_acceptance'],'burn_in_complete':False})
    print('Two source-preserving workflow upgrades verified; eight image families and other recorded gates remain')


if __name__=='__main__':main(sys.argv[1])
