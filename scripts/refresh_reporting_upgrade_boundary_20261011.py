"""Fresh read-only production boundary while private report qualification runs."""
import json
from datetime import datetime, timezone
import control_upgrade_main_dns_20261011 as c


def main():
    out=c.ROOT/'.work/platform-upgrade-20261011-stage34/boundary-refresh-v2'
    if out.exists(): raise ValueError('Preserve prior boundary refresh')
    plan=c.old.protected(c.OUT/'plan.json')
    current=c.boundary()
    if current!=plan['boundary']: raise ValueError('Live boundary drift; reconcile before any deployment')
    c.links(False)
    health=c.health()
    c.old.save(out,'result',{'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),
        'workloads_unchanged':len(current['workloads']),'website':health,'public_fallback_still_enabled':True,
        'main_rehearsal_dns_links_absent':True,'production_reports_changed':False,'credentials_changed':False,
        'stage34_candidate_deployed':False,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED',
        'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'workloads_unchanged':len(current['workloads']),'website':health,'full_upgrade_complete':False}))


if __name__=='__main__': main()
