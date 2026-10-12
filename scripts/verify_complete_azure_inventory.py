"""Read-only complete ARM/CLI inventory and active watchdog coverage review.

Records metadata only, never raw application arguments or secret bindings.
The protected receipt uses the retained archive, not a cancelled agent runtime.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, BASE, SUB
from ibkr_etl.azure_inventory import read_collection
from ibkr_etl.schedule_monitor import DAILY_V1, DAILY_V2, HEARTBEATS
from run_specialist_gateway_read_verification import retain

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'ibkrv2stg-agent-gateway': ('Microsoft.App/containerApps', '2024-03-01'),
    'ibkrv2stg-agent-report': ('Microsoft.App/jobs', '2024-03-01'),
    'ibkr-v2-specialist-daily-report-ready': ('Microsoft.Insights/scheduledQueryRules', '2023-12-01'),
    'ibkr-v2-specialist-daily-report-missing': ('Microsoft.Insights/scheduledQueryRules', '2023-12-01'),
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output-directory', type=Path, required=True)
    args = p.parse_args()
    folder = args.output_directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        raise ValueError('Choose an empty evidence directory')
    if az('account', 'show', '--query', 'id') != SUB:
        raise ValueError('Wrong subscription')
    page_counts = {}
    def transport(method, path):
        collection = path.split('?')[0]
        page_counts[collection] = page_counts.get(collection, 0) + 1
        return request(method, path)
    def collection(kind, version):
        return read_collection(transport, BASE + kind + '?api-version=' + version)
    jobs = collection('Microsoft.App/jobs', '2024-03-01')
    apps = collection('Microsoft.App/containerApps', '2024-03-01')
    rules = collection('Microsoft.Insights/scheduledQueryRules', '2023-12-01')
    resources = read_collection(transport,
        f'/subscriptions/{SUB}/resourceGroups/{RG}/resources?api-version=2021-04-01')
    cli_resources = az('resource', 'list', '-g', RG, '--query', '[].id')
    cli_jobs = az('containerapp', 'job', 'list', '-g', RG, '--query', '[].id')
    if {r['id'].casefold() for r in resources} != {r.casefold() for r in cli_resources}:
        raise ValueError('REST and CLI resource inventories disagree')
    if {r['id'].casefold() for r in jobs} != {r.casefold() for r in cli_jobs}:
        raise ValueError('REST and CLI job inventories disagree')
    for name, (kind, version) in TARGETS.items():
        if request('GET', BASE + kind + '/' + name + '?api-version=' + version, missing=True) is not None:
            raise ValueError('Cancelled target reappeared')
    references = []
    for row in jobs + apps:
        encoded = json.dumps(row, sort_keys=True).casefold()
        found = [name for name in TARGETS if name.casefold() in encoded]
        if found:
            references.append({'resource': row['name'], 'targets': found})
    rule_by_name = {r['name']: r for r in rules}
    coverage = {}
    for daily, rule in ((DAILY_V1, 'ibkr-v11-daily-schedule-watchdog'),
                        (DAILY_V2, 'ibkr-v2-daily-schedule-watchdog')):
        for name, offset, runtime in daily:
            coverage[name] = (f'{offset % 60} {offset // 60} * * *', runtime * 60, rule)
    for name in HEARTBEATS:
        coverage[name] = ('*/5 * * * *', None, 'ibkr-v2-background-heartbeat-watchdog')
    coverage['ibkrv2stg-metadata-monitor'] = ('7,37 * * * *', 180, 'ibkr-v2-metadata-monitor-watchdog')
    coverage['ibkrv2stg-autonomy-review'] = ('0 1 1 * *', None, 'ibkr-v2-monthly-autonomy-schedule-watchdog')
    scheduled, uncovered = [], []
    for row in jobs:
        config = row['properties']['configuration']
        if config['triggerType'] != 'Schedule':
            continue
        name = row['name']
        cron = config['scheduleTriggerConfig']['cronExpression']
        entry = {'name': name, 'cron': cron, 'retry_limit': config['replicaRetryLimit']}
        expected = coverage.get(name)
        if expected is None:
            uncovered.append(name)
        else:
            expected_cron, timeout, rule = expected
            if cron != expected_cron or (timeout is not None and config['replicaTimeout'] != timeout):
                raise ValueError('Scheduled watchdog contract drift: ' + name)
            props = rule_by_name[rule]['properties']
            groups = props['actions']['actionGroups']
            if props['enabled'] is not True or len(groups) != 1 or not groups[0].casefold().endswith('/actiongroups/ibkr-v11-daily-alerts'):
                raise ValueError('Watchdog not enabled/routed: ' + rule)
            entry['watchdog'] = rule
        scheduled.append(entry)
    missing = sorted(set(coverage) - {r['name'] for r in jobs})
    baseline = json.loads((ROOT / '.work/system-improvements-20261006/retirement-final-compute/prepared.json').read_text())['record']
    current = {r['name']: fingerprint({'identity': r.get('identity'),
        'configuration': r['properties'].get('configuration'), 'template': r['properties'].get('template')}) for r in jobs + apps}
    previous = baseline['non_target_configuration_sha256']
    drift = sorted(name for name, digest in previous.items() if current.get(name) != digest)
    record = dict(checked_at=datetime.now(timezone.utc).isoformat(), resource_count=len(resources),
        job_count=len(jobs), app_count=len(apps), query_rule_count=len(rules),
        inventory_pages=page_counts, rest_cli_identity_sets_equal=True, cancelled_targets_absent=True,
        complete_compute_cancelled_references=references, scheduled_jobs=scheduled,
        uncovered_scheduled_jobs=uncovered, expected_jobs_missing=missing,
        previous_retirement_checked_compute_count=len(previous), previous_checked_config_drift=drift,
        additional_compute_now_checked=sorted(set(current) - set(previous)),
        read_only=True, resources_changed=False, ibkr_source_writes=False,
        notice_delivery_not_tested=True, financial_freshness_not_certified=True)
    receipt = retain(record, 'complete-inventory-20261006')
    with (folder / 'verification.json').open('x', encoding='utf-8') as handle:
        json.dump({'record': record, 'protected_receipt': receipt}, handle, indent=2)
    print(json.dumps(record, indent=2))
    if references or uncovered or missing or drift:
        raise RuntimeError('Inventory findings require reconciliation before full acceptance')


if __name__ == '__main__':
    main()
