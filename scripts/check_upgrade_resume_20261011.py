"""Read-only live resume check; retain no secret values or log bodies."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import deploy_upgrade_cash_summary_20261010 as d
from plan_upgrade_owner_retirement_20261010 import listing

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261011-stage31/resume-v1'

def main():
    if OUT.exists(): raise ValueError('Preserve prior resume evidence')
    plan = d.c.protected(d.OUT / 'plan.json')
    last = d.c.protected(ROOT / '.work/platform-upgrade-20261010-stage30/closeout.json')
    resources = d.current_resources()
    current = d.bindings(resources)
    other = lambda m: {k: v for k, v in m.items() if k != 'ibkrv2stg-web'}
    web = next(r for r in resources if r['name'] == 'ibkrv2stg-web')
    auth = d.c.request('GET', d.PATH + '/authConfigs/current' + d.API)
    wp = web['properties']; drift = []
    if other(current) != other(plan['bindings']): drift.append('other_workloads')
    if wp['template']['containers'][0]['image'] != last['website_image']: drift.append('website_image')
    if d.web_hash(d.clean(web)) != plan['invariant']: drift.append('website_settings')
    if auth['properties'] != plan['auth']['properties']: drift.append('website_auth')
    revision = d.c.request('GET', d.PATH + '/revisions/' + wp['latestReadyRevisionName'] + d.API)
    def runs(item):
        config = item['properties']['configuration']
        if config.get('triggerType') != 'Schedule': return None
        rows = listing(item['id'] + '/executions', '2026-07-01')
        return {'job': item['name'], 'schedule': config.get('scheduleTriggerConfig'),
                'recent': [{'name': r['name'], 'status': r['properties'].get('status'),
                            'start': r['properties'].get('startTime'), 'end': r['properties'].get('endTime')}
                           for r in sorted(rows, key=lambda r: r['properties'].get('startTime', ''), reverse=True)[:3]]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        schedules = [r for r in pool.map(runs, resources) if r]
    value = {'captured_at': datetime.now(timezone.utc).isoformat(), 'read_only_live_check': True,
             'previous_checkpoint': last['protected_receipt'], 'drift': drift, 'bindings': current,
             'web_revision': wp['latestReadyRevisionName'], 'web_health': revision['properties']['healthState'],
             'web_traffic': wp['configuration']['ingress']['traffic'], 'scheduled_jobs': schedules,
             'observation_14_day': 'UNVERIFIED', 'real_viewer_sign_in': 'UNVERIFIED',
             'whole_scheduled_cycle': 'UNVERIFIED', 'human_alert_receipt': 'UNVERIFIED', 'full_upgrade_complete': False}
    d.c.save(OUT, 'result', value)
    print(json.dumps({k: v for k, v in value.items() if k not in ('bindings', 'previous_checkpoint')}))

if __name__ == '__main__': main()
