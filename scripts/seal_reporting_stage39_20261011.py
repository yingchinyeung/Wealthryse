"""Seal exact approved retirement with the resulting 32-caller network boundary."""
import copy
import inspect
import json
from datetime import datetime, timezone
import apply_reporting_retirement_20261011 as a
import control_upgrade_main_dns_20261011 as c

OUT=a.ROOT/'.work/platform-upgrade-20261011-stage39'

def boundary_after_retirement():
    # Preserve every network/authentication check. Only the reviewed caller
    # count changes after removal of the exact two approved definitions.
    source=inspect.getsource(c.boundary)
    if source.count('len(callers)!=34')!=1:
        raise ValueError('Original boundary contract changed')
    scope=dict(vars(c))
    exec(compile(source.replace('len(callers)!=34','len(callers)!=32'),
                 'post-retirement-boundary','exec'),scope)
    return scope['boundary']()

def expected_boundary(prior):
    wanted=copy.deepcopy(prior)
    for name in a.p.IMAGES:
        if name not in wanted['workloads'] or name not in wanted['callers']:
            raise ValueError('Retirement target absent from previous boundary')
        del wanted['workloads'][name]
        wanted['callers'].remove(name)
    return wanted

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve checkpoint')
    verified=a.r.protected(a.OUT/'verified.json')
    if not verified['passed'] or verified['retired_jobs']!=sorted(a.p.IMAGES):raise ValueError('Verified retirement required')
    prior=a.r.protected(a.ROOT/'.work/platform-upgrade-20261011-stage38/closeout.json')
    current=boundary_after_retirement()
    if current!=expected_boundary(prior['boundary']):raise ValueError('Non-retirement boundary change')
    plan=a.r.protected(a.OUT/'plan.json')
    _,deps,aux,_=a.r.dependency_check()
    a.guard_dependencies(deps)
    without_alert=lambda rows:[r for r in rows if r['id'].lower()!=a.ALERT_PATH.lower()]
    if without_alert(aux)!=without_alert(plan['auxiliary_callers']):raise ValueError('Other monitoring or registry task changed')
    c.links(False);health=c.health()
    a.r.save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'boundary':current,'website':health,'retirement_receipt':verified['protected_receipt'],
        'retired_jobs':verified['retired_jobs'],'archived_executions':verified['archived_executions'],
        'disabled_monitor':verified['disabled_monitor'],'other_alerts_registry_tasks_preserved':True,
        'production_reports_or_approvals_changed':False,'production_database_writes':0,'broker_data_changed':False,
        'tracked_reporting_consumer_image_corrections_deployed':True,
        'remaining_gates':[x for x in prior['remaining_gates'] if not x.startswith('Retire two')],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
        'whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
        'independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_native_visual_review':'UNVERIFIED',
        'full_upgrade_complete':False})
    print(json.dumps({'checkpoint_evidence_verified':True,'workloads':len(current['workloads']),
        'main_network_callers':len(current['callers']),'website':health,'full_upgrade_complete':False}))

if __name__=='__main__':main()
