"""Read-only acceptance of the observed cron execution, separate from burn-in."""
from datetime import datetime, timezone
import json
import sys
from control_upgrade_metadata_runtime_20261010 import OUT, PATH, API, JOB, candidate, save, validate_completion
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from audit_azure_runtime import az


def main():
    execution = sys.argv[1]
    if not execution.startswith(JOB + '-') or not execution[len(JOB) + 1:].isdigit():
        raise ValueError('Observed scheduled execution name required')
    image = candidate(); plan = protected(OUT / 'deployment-v1/plan.json')
    job = request('GET', PATH + API)
    run = request('GET', PATH + '/executions/' + execution + API)
    p = run['properties']; start = datetime.fromisoformat(p['startTime'].replace('Z', '+00:00'))
    if start.minute not in (7, 37) or start.second or int(start.timestamp() // 60) != int(execution[len(JOB) + 1:]):
        raise ValueError('Execution name/time does not match cron cadence')
    actual = p['template']['containers'][0]; installed = job['properties']['template']['containers'][0]
    if p['status'] != 'Succeeded' or actual['image'] != image or any(actual.get(k) != installed.get(k) for k in ('command', 'args')) or settings_invariant(job) != plan['invariant']:
        raise ValueError('Scheduled execution or current settings differ')
    prefix = 'METADATA_MONITOR '
    q = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = [json.loads(r['Result']) for r in az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', q)]
    completions = [r for r in rows if r.get('kind') == 'complete']
    if len(completions) != 1: raise ValueError('One complete scheduled proof required')
    proof = completions[0]; records = [r for r in rows if r.get('kind') != 'complete']
    validate_completion(proof, records)
    folder = OUT / 'scheduled-acceptance-v1'
    if folder.exists(): raise ValueError('Preserve existing scheduled evidence')
    folder.mkdir()
    save(folder, 'result', {'passed': True, 'execution': execution, 'start': start.isoformat(), 'image': image,
        'observed_cron_named_execution': True, 'proof': proof, 'rows': records, 'saved_settings_preserved': True,
        'complete_platform_scheduled_cycle_verified': False, 'burn_in_complete': False,
        'full_upgrade_complete': False, 'checked_at': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'passed': True, 'execution': execution, 'records': len(records), 'full_upgrade_complete': False}))


if __name__ == '__main__': main()
