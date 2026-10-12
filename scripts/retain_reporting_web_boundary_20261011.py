"""Rebase the protected workload checkpoint after the verified website rollout."""
import json
import control_upgrade_main_dns_20261011 as c
import deploy_reporting_sector_web_20261011 as w

def main():
    out=w.STAGE/'web-boundary-v1'
    if out.exists():raise ValueError('Preserve prior checkpoint')
    prior=w.protected(w.BEFORE/'resume-v1/result.json')['boundary']
    plan=w.protected(w.OUT/'plan.json');verified=w.protected(w.OUT/'verified.json')
    current=c.boundary()
    w.unrelated(current['workloads'],prior['workloads'])
    if {k:v for k,v in current.items() if k!='workloads'}!={k:v for k,v in prior.items() if k!='workloads'}:raise ValueError('Non-workload boundary drift')
    live=w.b.request('GET',w.PATH+w.API);p=live['properties']
    if not verified['passed'] or p['template']['containers'][0]['image']!=verified['image'] or p['latestReadyRevisionName']!=verified['revision'] or w.web_hash(w.clean(live))!=plan['invariant']:raise ValueError('Exact verified website required')
    c.links(False);health=c.health()
    w.save(out,'result',{'passed':True,'boundary':current,'website':health,'web_deployment':verified['protected_receipt'],
        'only_website_image_revision_changed':True,'reason':'Generic workload binding includes revision metadata; retain the exact verified new binding rather than relaxing any hash comparison.',
        'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'website':health,'workloads':len(current['workloads'])}))

if __name__=='__main__':main()
