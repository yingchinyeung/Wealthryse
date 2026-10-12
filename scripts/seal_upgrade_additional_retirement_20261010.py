"""Close approved diagnostic retirement; keep all remaining gates explicit."""
from datetime import datetime,timezone
import json
from pathlib import Path
import retire_upgrade_additional_diagnostics_20261010 as r
from audit_upgrade_image_coverage_20261009 import inventory
from audit_azure_runtime import az


def main():
    out=r.OUT.parent
    if (out/'closeout.json').exists():raise ValueError('Preserve sealed stage')
    retirement=r.protected(r.OUT/'verified.json');plan=r.protected(r.OUT/'plan.json')
    stage19=r.protected(r.ROOT/'.work/platform-upgrade-20261010-stage19/closeout.json')
    if retirement.get('passed') is not True or retirement['retired_count']!=7 or set(retirement['retired_jobs'])!=r.APPROVED:
        raise ValueError('Exact seven-job verification required')
    current=r.current_resources();r.preserve_others(current,plan)
    if any(x['name'] in r.APPROVED for x in current):raise ValueError('Retired job still present')
    rows=inventory();images={x['image'] for x in rows};remaining=[]
    for item in stage19['remaining_blocked_or_unqualified_image_families']:
        wanted=dict(item);wanted['jobs']=[n for n in item['jobs'] if n not in r.APPROVED]
        if not wanted['jobs']:continue
        if item['declared_reference'] not in images:raise ValueError('Unexpected image change')
        if {x['name'] for x in rows if x['image']==item['declared_reference']}!=set(wanted['jobs']):raise ValueError('Image binding mismatch')
        if wanted['mutable_reference']:
            current_digest=az('acr','repository','show','-n','ibkrv2stgauhecwc4itaoo','--image',item['declared_reference'].split('/',1)[1],'--query','digest')
            if current_digest!=item['resolved_image'].split('@')[1]:raise ValueError('Mutable tag moved after scan')
        remaining.append(wanted)
    if len(rows)!=36 or len(images)!=28 or len(remaining)!=10:raise ValueError('Unexpected inventory totals')
    if inventory()!=rows:raise ValueError('Inventory changed during closeout')
    baselines=[r.protected(r.OUT/name/'baseline.json') for name in sorted(r.APPROVED)]
    roles={a['id']:a for b in baselines for a in b['role_assignments']}
    # Every archived assignment is inherited, not scoped to a retired job.
    if any(a['properties']['scope'].lower().startswith(r.path_for(b['name']).lower()) for a in roles.values() for b in baselines):
        raise ValueError('Resource-scoped role needs separate reconciliation')
    current_roles={a['id']:a for a in r.listing(r.BASE+'Microsoft.Authorization/roleAssignments','2022-04-01')}
    for key,a in roles.items():
        if current_roles.get(key)!=a:raise ValueError('Inherited role assignment changed')
    record={'stage':'seven-authorized-diagnostic-retirements','sealed_at':datetime.now(timezone.utc).isoformat(),
        'retirement_receipt':retirement['protected_receipt'],'previous_upgrade_receipt':stage19['protected_receipt'],
        'retired_jobs':sorted(r.APPROVED),'archived_executions':retirement['archived_executions'],
        'all_other_app_job_settings_and_images_preserved':True,'inherited_role_assignments_preserved':len(roles),
        'identities_databases_reports_deleted':False,'restore_receipt':retirement['restore_receipt'],
        'recovery_limit':'Job definitions and execution evidence are archived. Original Azure execution IDs cannot be recreated; existing Log Analytics logs remain untouched.',
        'current_inventory':{'definitions':36,'unique_images':28,'passing_scan_image_families':18,
            'blocked_assessment_image_families':sum(x['assessment_status']=='blocked' for x in remaining),
            'unqualified_coverage_image_families':sum(x['assessment_status']=='unqualified' for x in remaining)},
        'remaining_images':remaining,'remaining_gates':stage19['remaining_gates'],
        'private_network_cutover_performed':False,'real_viewer_acceptance':'Explicitly unverified by owner choice',
        'burn_in_complete':False,'full_upgrade_complete':False}
    r.save(out,'closeout',record)
    print(json.dumps({'sealed':True,'retired':7,'definitions':36,'remaining_image_families':10,'full_upgrade_complete':False}))


if __name__=='__main__':main()
