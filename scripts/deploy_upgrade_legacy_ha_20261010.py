"""Journaled legacy PostgreSQL availability upgrade after clone qualification.

Only compute and HA may change. No connection strings, data, jobs, public
network paths, authentication, regions or backup policy are changed.
"""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from deploy_viewer_private_admin_20261008 import request, rid
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain
import deploy_t_runtime_release_20261009 as runtime
import inventory_upgrade_callers_20261009 as inventory

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage12/legacy-ha-live-v1'
QUALIFIED = ROOT / '.work/platform-upgrade-20261009-stage10'
TARGET = rid('Microsoft.DBforPostgreSQL/flexibleServers', 'ibkrv11-pg-5rcc62xxqblay')
API = '?api-version=2024-08-01'
APP_API = '?api-version=2024-03-01'
SKU = {'name': 'Standard_D2ds_v5', 'tier': 'GeneralPurpose'}
ORIGINAL = {'name': 'Standard_B1ms', 'tier': 'Burstable'}
JOBS = {'ibkr-v11-bootstrap', 'ibkr-v11-daily', 'ibkr-v11-history-backfill'}
ENVIRONMENT = rid('Microsoft.App/managedEnvironments', 'ibkrv11-env')
runtime.request = request
inventory.request = request


def invariant(server):
    value = copy.deepcopy(server)
    props = value['properties']
    for name in ('state', 'highAvailability', 'availabilityZone'):
        props.pop(name, None)
    props.get('backup', {}).pop('earliestRestoreDate', None)
    return {k: value.get(k) for k in ('id', 'location', 'identity', 'tags', 'properties')}


def guard(server):
    p = server['properties']
    if server['id'].lower() != TARGET.lower() or server['location'].replace(' ', '').lower() != 'eastasia':
        raise ValueError('Exact authorized East Asia legacy server required')
    if p['network']['publicNetworkAccess'] != 'Disabled' or p['network']['delegatedSubnetResourceId'].lower() != rid('Microsoft.Network/virtualNetworks/subnets', 'ibkrv11-vnet/postgres').lower():
        raise ValueError('Original private network must remain unchanged')
    if p['backup']['backupRetentionDays'] != 35 or p['backup']['geoRedundantBackup'] != 'Disabled':
        raise ValueError('Original East Asia backup policy required')


def save(name, record):
    value = {**record, 'protected_receipt': retain(record, 'upgrade-legacy-live-' + name)}
    with (OUT / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2)


def load(name):
    return protected(OUT / (name + '.json'))


def callers():
    result = {}
    for kind in ('Microsoft.App/jobs', 'Microsoft.App/containerApps'):
        for item in inventory.listing(kind, '2024-03-01'):
            p = item['properties']
            env = p.get('environmentId') or p.get('managedEnvironmentId') or ''
            if env.lower() != ENVIRONMENT.lower():
                continue
            if kind != 'Microsoft.App/jobs' or item['name'] not in JOBS:
                raise ValueError('Unexpected legacy environment caller; investigate before maintenance')
            if p.get('provisioningState') != 'Succeeded':
                raise ValueError('Legacy job not ready')
            runtime.idle(item['name'])
            result[item['name']] = settings_invariant(item)
    if set(result) != JOBS:
        raise ValueError('Incomplete legacy caller inventory')
    return result


def mutation_body(stage, server, plan):
    p = server['properties']
    if p['state'] != 'Ready':
        raise ValueError('Server must be Ready; reconcile outstanding operation')
    if stage == 'scale':
        if server['sku'] != ORIGINAL or p['highAvailability']['mode'] != 'Disabled':
            raise ValueError('Unexpected scale baseline')
        return {'sku': SKU}
    if stage == 'enable':
        if server['sku'] != SKU or p['highAvailability']['mode'] != 'Disabled' or p['availabilityZone'] != plan['primary_zone']:
            raise ValueError('Scaled original primary required')
        return {'properties': {'availabilityZone': plan['primary_zone'], 'highAvailability': {'mode': 'ZoneRedundant', 'standbyAvailabilityZone': plan['standby_zone']}}}
    raise ValueError('Only qualified compute and HA mutations are supported')


def main(stage):
    server = request('GET', TARGET + API)
    guard(server)
    if stage == 'plan':
        if OUT.exists():
            raise ValueError('Existing plan must be reconciled, never overwritten')
        runtime.safe_window()
        rollback = protected(QUALIFIED / 'legacy-ha-rehearsal-v1/verify-rollback-result.json')
        failover = protected(QUALIFIED / 'legacy-ha-rehearsal-v1/verify-failover-result.json')
        tables = protected(QUALIFIED / 'legacy-ha-rollback-read-v1/result.json')
        dispatch = protected(QUALIFIED / 'legacy-ha-rollback-read-v1/dispatch.json')
        run = request('GET', rid('Microsoft.App/jobs', 'ibkr-v11-daily') + '/executions/' + dispatch['execution'] + APP_API)
        ended = datetime.fromisoformat(run['properties']['endTime'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - ended).total_seconds()
        if not 0 <= age <= 3600 or run['properties']['status'] != 'Succeeded':
            raise ValueError('Fresh completed recovery comparison required')
        if not all(x['passed'] for x in (rollback, failover, tables)) or len(tables['proof']['source']['tables']) != 25 or tables['proof']['table_differences']:
            raise ValueError('Qualified failover, rollback and exact 25-table preservation required')
        if server['sku'] != ORIGINAL or server['properties']['highAvailability']['mode'] != 'Disabled' or server['properties']['state'] != 'Ready':
            raise ValueError('Original production baseline required')
        dependencies = callers()
        primary = server['properties']['availabilityZone']
        if primary != '3':
            raise ValueError('Requalify unexpected primary zone')
        OUT.mkdir(parents=True)
        save('plan', {'target': TARGET, 'before_invariant': invariant(server), 'before_sku': server['sku'], 'before_ha': server['properties']['highAvailability'], 'primary_zone': primary, 'standby_zone': '1', 'desired_sku': SKU, 'caller_invariants': dependencies, 'preflight_tables': tables['proof']['source'], 'clone_rollback': rollback['protected_receipt'], 'clone_failover': failover['protected_receipt'], 'recovery_comparison': tables['protected_receipt'], 'created_at': datetime.now(timezone.utc).isoformat(), 'authorization': 'User requested the full upgrade, approved immediate maintenance and additional cost, and restricted recovery copies to East Asia.', 'rollback': {'disable_ha': {'properties': {'highAvailability': {'mode': 'Disabled'}}}, 'then_restore_compute': {'sku': ORIGINAL}, 'conditions': 'Only if needed after a failed live acceptance; first reconcile in-flight operation, then Ready after each step, then repeat all-table read-only comparison. Do not blindly retry unknown writes or redirect production to clone.'}, 'scope': 'Legacy database compute and same-region zone redundancy only. Existing private endpoint, credentials, database contents, job settings and backup retention preserved.'})
        print('Prepared protected legacy database availability plan; no live settings changed')
        return
    plan = load('plan')
    if invariant(server) != plan['before_invariant']:
        raise ValueError('Non-compute/HA production settings drifted')
    if stage == 'state':
        print(json.dumps({'state': server['properties']['state'], 'sku': server['sku'], 'ha': server['properties']['highAvailability'], 'zone': server['properties']['availabilityZone']}))
        return
    if stage in ('scale', 'enable'):
        if (OUT / (stage + '-intent.json')).exists():
            raise ValueError('Mutation already journaled: reconcile, never redispatch')
        runtime.safe_window()
        if callers() != plan['caller_invariants']:
            raise ValueError('Legacy callers changed')
        if stage == 'enable' and not load('verify-scale')['passed']:
            raise ValueError('Compute verification required')
        body = mutation_body(stage, server, plan)
        save(stage + '-intent', {'target': TARGET, 'body': body, 'started_at': datetime.now(timezone.utc).isoformat(), 'caller_invariants': plan['caller_invariants']})
        ack = request('PATCH', TARGET + API, data=body)
        save(stage + '-ack', {'id': ack.get('id'), 'state': ack.get('properties', {}).get('state')})
        print('Submitted exactly one legacy database ' + stage + ' request; reconcile before continuing')
        return
    if stage not in ('verify-scale', 'verify-ha', 'seal'):
        raise ValueError('Unknown phase')
    if server['properties']['state'] != 'Ready':
        print('Database maintenance is still in progress')
        return
    if server['sku'] != SKU:
        raise ValueError('Qualified compute not installed')
    if stage != 'verify-scale':
        ha = server['properties']['highAvailability']
        if ha.get('mode') != 'ZoneRedundant' or ha.get('state') != 'Healthy':
            print(json.dumps({'ha': ha, 'passed': False}))
            return
        if {server['properties']['availabilityZone'], ha['standbyAvailabilityZone']} != {plan['primary_zone'], plan['standby_zone']}:
            raise ValueError('Unexpected HA zones')
    if callers() != plan['caller_invariants']:
        raise ValueError('Legacy caller configuration drift')
    record = {'passed': True, 'target': TARGET, 'sku': server['sku'], 'ha': server['properties']['highAvailability'], 'zone': server['properties']['availabilityZone'], 'non_compute_ha_settings_preserved': True, 'caller_settings_preserved': True, 'checked_at': datetime.now(timezone.utc).isoformat()}
    if stage == 'seal':
        proof = protected(OUT / 'postflight-v1/result.json')
        if not proof['passed'] or proof['proof']['source'] != plan['preflight_tables'] or proof['proof']['table_differences']:
            raise ValueError('Exact preflight-to-postflight source preservation required')
        record['data_preservation'] = proof['protected_receipt']
        record['full_upgrade_complete'] = False
    if not (OUT / (stage + '.json')).exists():
        save(stage, record)
    print(json.dumps({'passed': True, 'phase': stage}))


if __name__ == '__main__':
    main(sys.argv[1])
