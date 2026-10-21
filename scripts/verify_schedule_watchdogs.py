"""Read-only live/shadow verification; writes local deployment parameters only."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az, RG
from ibkr_etl.schedule_monitor import DAILY_V2, HEARTBEATS, daily_query, heartbeat_query
from ibkr_etl.azure_inventory import read_collection
from deploy_metadata_monitor import request, BASE

ROOT = Path(__file__).resolve().parents[1]
WORKSPACES = {
    'ibkrv2stg-logs': '5264a124-10cd-45f6-93a5-6d65c37e9bfb',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable', action='store_true', help='Prepare enabled parameters; does not deploy')
    parser.add_argument('--verify-deployed', action='store_true', help='Read back deployed rule configuration')
    parser.add_argument('--output-directory', type=Path, default=ROOT / '.work' / 'observability-20261001',
                        help='Use a new evidence directory to preserve earlier qualification')
    args = parser.parse_args()
    folder = args.output_directory.resolve()
    if any((folder / name).exists() for name in ('verification.json', 'parameters.json')):
        raise RuntimeError('Evidence already exists; choose a new output directory')
    if az('account', 'show', '--query', 'id') != '3956e821-cee4-403f-8708-e84e2ed490eb':
        raise RuntimeError('Wrong subscription')
    jobs = az('containerapp', 'job', 'list', '-g', RG, '--query',
              '[].{name:name,trigger:properties.configuration.triggerType,cron:properties.configuration.scheduleTriggerConfig.cronExpression,timeout:properties.configuration.replicaTimeout}')
    by_name = {row['name']: row for row in jobs}
    for name, offset, runtime in DAILY_V2:
        observed = by_name[name]
        if (observed['trigger'] != 'Schedule' or observed['cron'] != f'{offset % 60} {offset // 60} * * *'
                or observed['timeout'] != runtime * 60):
            raise RuntimeError('Schedule/timeout drift; review watchdog before deploying: ' + name)
    for name in HEARTBEATS:
        if by_name[name]['trigger'] != 'Schedule' or by_name[name]['cron'] != '*/5 * * * *':
            raise RuntimeError('Heartbeat schedule drift: ' + name)
    # v1.1's ibkr-v11-daily and its watchdog rule on ibkrv11-logs were deleted with v1.1 on 21 Oct 2026.
    rules = [
        {'name': 'ibkr-v2-daily-schedule-watchdog', 'workspaceName': 'ibkrv2stg-logs', 'query': daily_query(DAILY_V2)},
        {'name': 'ibkr-v2-background-heartbeat-watchdog', 'workspaceName': 'ibkrv2stg-logs', 'query': heartbeat_query()},
    ]
    evidence = {'checked_at': datetime.now(timezone.utc).isoformat(), 'queries': []}
    if args.verify_deployed:
        resources = read_collection(request, BASE + 'Microsoft.Insights/scheduledQueryRules?api-version=2023-12-01')
        observed = {row['name']: row['properties'] for row in resources}
        evidence['deployed_rules'] = []
        for rule in rules:
            properties = observed[rule['name']]
            criterion = properties['criteria']['allOf'][0]
            action_groups = properties['actions']['actionGroups']
            if (properties['enabled'] is not True or properties['evaluationFrequency'] != 'PT15M'
                    or properties['autoMitigate'] is not True or properties['severity'] != 2
                    or criterion['query'].strip() != rule['query'].strip()
                    or len(action_groups) != 1
                    or not action_groups[0].lower().endswith('/actiongroups/ibkrv2stg-alerts')):
                raise RuntimeError('Deployed watchdog differs from reviewed settings')
            evidence['deployed_rules'].append({'name': rule['name'], 'enabled': True, 'query_verified': True})
    for rule in rules:
        workspace = WORKSPACES[rule['workspaceName']]
        rows = az('monitor', 'log-analytics', 'query', '-w', workspace, '--analytics-query', rule['query'])
        # Exercise a genuinely empty log stream, rather than treating no
        # application output as success. No simulation creates an Azure alert.
        empty = 'datatable(TimeGenerated:datetime,JobName_s:string,Reason_s:string,ExecutionName_s:string)[]'
        simulated = az('monitor', 'log-analytics', 'query', '-w', workspace,
                       '--analytics-query', rule['query'].replace('ContainerAppSystemLogs_CL', empty))
        expected = len(HEARTBEATS) if 'heartbeat' in rule['name'] else len(DAILY_V2)
        if len(simulated) != expected:
            raise RuntimeError('Synthetic missing-telemetry detection failed')
        evidence['queries'].append({'name': rule['name'], 'live_issues': rows, 'synthetic_issues': len(simulated)})
        print(rule['name'], 'live issues:', len(rows), 'synthetic issues detected:', len(simulated), flush=True)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'verification.json').open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(evidence, indent=2))
    if args.enable and any(item['live_issues'] for item in evidence['queries']):
        raise RuntimeError('Review live issues before enabling notifications')
    parameters = {
        '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
        'contentVersion': '1.0.0.0', 'parameters': {'rules': {'value': rules}, 'enabled': {'value': args.enable}},
    }
    with (folder / 'parameters.json').open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(parameters, indent=2))
    print('Saved verification and parameters; no resource writes or job starts')


if __name__ == '__main__':
    main()
