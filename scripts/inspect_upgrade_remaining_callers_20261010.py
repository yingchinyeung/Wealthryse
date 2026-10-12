"""Bounded read-only caller classification for unresolved image families.

This is not retirement approval, a complete external-caller proof, or permission
to change a job. Persist fingerprints and metadata, never secrets or payloads.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from audit_upgrade_image_coverage_20261009 import inventory
from control_upgrade_legacy_recovery_probe_20261009 import protected
from plan_upgrade_owner_retirement_20261010 import listing, refs, BASE
from inspect_upgrade_owner_payloads_20261010 import decode
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage19'
API = '2026-07-01'


def main():
    if (OUT/'caller-inspection.json').exists(): raise ValueError('Preserve prior evidence')
    intent = protected(OUT/'intent.json')
    before = inventory()
    if before != intent['templates']: raise ValueError('Runtime drift')
    checkpoint = protected(ROOT/'.work/platform-upgrade-20261010-stage18/image-coverage-v1.json')
    names = sorted({n for i in checkpoint['images'] if not i['protected_scan_verified'] for n in i['templates']})
    resources = listing(BASE+'Microsoft.App/jobs', API)+listing(BASE+'Microsoft.App/containerApps', API)
    by_name = {r['name']: r for r in resources}
    callers, errors = [], []
    for r in resources:
        found = refs(r, names)
        for c in r['properties']['template']['containers']:
            argv = (c.get('command') or [])+(c.get('args') or [])
            if '-c' in argv and argv[-1]:
                try:
                    for text in decode(argv[-1]): found += refs(text, names)
                except (ValueError, SyntaxError) as exc:
                    errors.append({'caller': r['name'], 'error_type': type(exc).__name__})
        found = sorted(set(found)-{r['name']})
        if found: callers.append({'caller': r['name'], 'targets': found})
    def inspect(name):
        r = by_name[name]; config = r['properties']['configuration']; c = r['properties']['template']['containers'][0]
        runs = listing(rid('Microsoft.App/jobs', name)+'/executions', API)
        latest = sorted(runs, key=lambda x:x['properties'].get('startTime',''), reverse=True)[:5]
        return {'name': name, 'image': c['image'], 'settings_invariant_sha256': settings_invariant(r),
            'trigger': config['triggerType'], 'identity_type': r.get('identity',{}).get('type'),
            'registry_count': len(config.get('registries') or []), 'secret_reference_count': len(config.get('secrets') or []),
            'command': c.get('command'), 'arguments_sha256': hashlib.sha256(json.dumps(c.get('args'),sort_keys=True).encode()).hexdigest(),
            'script_entrypoint': next((s for s in (c.get('args') or []) if s.startswith('scripts/') and s.endswith('.py')), None),
            'active_executions': [x['name'] for x in runs if x['properties']['status'] not in ('Succeeded','Failed','Stopped','Canceled','Cancelled')],
            'recent_executions': [{'name': x['name'], 'status': x['properties']['status'], 'start': x['properties'].get('startTime'),
                                   'end': x['properties'].get('endTime')} for x in latest]}
    with ThreadPoolExecutor(max_workers=3) as pool: jobs = list(pool.map(inspect, names))
    if inventory() != before: raise ValueError('Runtime inventory changed during inspection')
    for item in jobs:
        live = request('GET',rid('Microsoft.App/jobs', item['name'])+'?api-version='+API)
        if settings_invariant(live) != item['settings_invariant_sha256']: raise ValueError('Job settings drifted during inspection')
    record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'jobs': jobs,
        'app_job_template_callers': callers, 'unparsed_inline_callers': errors,
        'limitations': ['External operators, CI/CD and non-container schedulers not cleared by this inspection.',
                       'Old execution dates do not establish that a job is obsolete.',
                       'Retirement approval for the earlier 22 jobs does not extend to these definitions.'],
        'jobs_started': 0, 'settings_changed': False, 'retirement_authorized': False}
    receipt = retain(record, 'upgrade-stage19-caller-inspection')
    with (OUT/'caller-inspection.json').open('x',encoding='utf-8') as f: json.dump({**record,'protected_receipt':receipt},f,indent=2)
    print(json.dumps({'jobs':len(jobs),'callers':callers,'unparsed_inline_callers':errors,
                      'active_executions':sum(len(x['active_executions']) for x in jobs)}),flush=True)


if __name__ == '__main__': main()
