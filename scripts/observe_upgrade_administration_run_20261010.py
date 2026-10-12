"""Archive one existing normal run; never start work or claim full burn-in."""
import json
from datetime import datetime
import control_upgrade_administration_runtime_20261010 as c


def main():
    folder = c.OUT / 'normal-run-v1'
    if folder.exists(): raise ValueError('Preserve existing observation')
    image, _, _ = c.candidate('admin')
    job = c.TARGETS['admin'][0]; name = job + '-29860095'
    run = c.request('GET', c.rid('Microsoft.App/jobs', job) + '/executions/' + name + c.API)
    container = run['properties']['template']['containers'][0]
    if run['properties']['status'] != 'Succeeded' or container.get('image') != image or container.get('command') != ['python'] or container.get('args') != ['scripts/run_autonomous_administrator.py']:
        raise ValueError('Exact installed normal run required')
    started = datetime.fromisoformat(run['properties']['startTime'].replace('Z', '+00:00'))
    applied = c.protected(c.OUT / 'admin-runtime-v1/deployment-v1/apply-intent.json')
    if started <= datetime.fromisoformat(applied['started_at']) or started.minute % 5 or started.second:
        raise ValueError('Normal run does not match post-update schedule boundary')
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + name + "-' | extend p=parse_json(Log_s) | where isnotempty(p.status) | project status=tostring(p.status), finding_count=toint(p.finding_count), new_incident_count=toint(p.new_incident_count), model_used=tobool(p.model_used), actions_count=array_length(p.actions), error_code=tostring(p.error_code)"
    rows = c.az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1: raise ValueError('One bounded normal-run result required')
    row = rows[0]
    if row['status'] != 'HEALTHY' or any(str(row[k]) != '0' for k in ('finding_count', 'new_incident_count', 'actions_count')) or str(row['model_used']).lower() != 'false' or row['error_code']:
        raise ValueError('Normal run not healthy without actions')
    c.save(folder, 'result', {'passed': True, 'execution': name, 'image': image, 'status': 'HEALTHY',
        'start_time': run['properties']['startTime'], 'end_time': run['properties'].get('endTime'),
        'normal_entrypoint_verified': True, 'schedule_boundary_matches': True, 'manually_started_by_this_upgrade': False,
        'findings': 0, 'repair_actions': 0, 'model_used': False, 'whole_platform_daily_cycle_qualified': False,
        'burn_in_complete': False, 'full_upgrade_complete': False})
    print('Existing 04:15 normal run archived: HEALTHY, zero findings/actions, no model call')


if __name__ == '__main__': main()
