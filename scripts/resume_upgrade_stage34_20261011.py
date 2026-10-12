"""Read-only live resume check before any further upgrade actions."""
import json
import control_upgrade_main_dns_20261011 as c
OUT=c.ROOT/'.work/platform-upgrade-20261011-stage34/resume-v1'
def main():
    if OUT.exists():raise ValueError('Preserve resume evidence')
    last=c.old.protected(c.ROOT/'.work/platform-upgrade-20261011-stage33/closeout.json')
    plan=c.old.protected(c.OUT/'plan.json')
    current=c.boundary()
    if current!=plan['boundary']:raise ValueError('Live drift: investigate before changes')
    c.links(False);health=c.health()
    c.old.save(OUT,'result',{'passed':True,'previous':last['protected_receipt'],'boundary':current,
        'website':health,'changes_made':False,'real_viewer_sign_in':'UNVERIFIED','observation_14_day':'UNVERIFIED'})
    print(json.dumps({'passed':True,'website':health,'workloads':len(current['workloads'])}))
if __name__=='__main__':main()
