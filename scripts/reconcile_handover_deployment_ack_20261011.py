"""Reconcile asynchronous PATCH response without another mutation."""
import json
from datetime import datetime,timezone
import deploy_report_handover_20261011 as d
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
def main():
    plan=protected(d.OUT/'plan.json');intent=protected(d.OUT/'apply-intent.json');verified=protected(d.OUT/'verified.json')
    if intent['plan_receipt']!=plan['protected_receipt'] or not verified['passed'] or verified['image']!=plan['image']:raise ValueError('Exact deployment evidence required')
    live=d.b.request('GET',d.PATH+d.API)
    if live['properties']['template']['containers'][0]['image']!=plan['image'] or live['properties']['provisioningState']!='Succeeded' or live['properties']['latestReadyRevisionName']!=verified['revision']:raise ValueError('Actual deployment not successful')
    if (d.OUT/'apply-ack.json').exists():raise ValueError('Ordinary acknowledgement already exists')
    save(d.OUT,'apply-reconciled',{'passed':True,'verified_at':datetime.now(timezone.utc).isoformat(),'image':plan['image'],'revision':verified['revision'],
        'reason':'Initial PATCH returned without properties; controller raised KeyError while recording its acknowledgement. Actual immutable image, ready revision, health, traffic and authentication were subsequently verified.',
        'deployment_resubmitted':False,'live_provisioning_state':'Succeeded','verification_receipt':verified['protected_receipt']})
    print(json.dumps({'reconciled':True,'deployment_resubmitted':False}))
if __name__=='__main__':main()
