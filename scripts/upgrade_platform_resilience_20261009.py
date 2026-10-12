"""Additive, owner-authorized East Asia resilience changes, with drift guards.

Never modifies public access, credentials, reports, replication regions or HA.
Intents are create-only: reconcile an ambiguous response instead of retrying.
"""
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from deploy_metadata_monitor import request, BASE
from run_specialist_gateway_read_verification import retain

OUT = Path(__file__).resolve().parents[1] / '.work/platform-upgrade-20261009-stage2/resilience-v1'
REGISTRIES = ('ibkrv115rcc62xxqblay', 'ibkrv2stgauhecwc4itaoo')
DATABASES = ('ibkrv11-pg-5rcc62xxqblay', 'ibkrv2stg-pg-auhecwc4itaoo')
WORKSPACE = BASE + 'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
DIAGNOSTIC = 'platform-security-audit-v1'
APIS = {'registry': ('Microsoft.ContainerRegistry/registries/', '2023-07-01'),
        'database': ('Microsoft.DBforPostgreSQL/flexibleServers/', '2024-08-01')}

def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, sort_keys=True)

def read(name):
    return json.loads((OUT / (name + '.json')).read_text())

def path(kind, name):
    if name not in (REGISTRIES if kind == 'registry' else DATABASES):
        raise ValueError('Resource outside exact allowlist')
    typ, version = APIS[kind]
    return BASE + typ + name, '?api-version=' + version

def normalized(value, kind):
    result = copy.deepcopy(value)
    for key in ('etag', 'systemData'):
        result.pop(key, None)
    props = result['properties']
    props.pop('provisioningState', None)
    if kind == 'registry':
        result.pop('sku', None)
    else:
        # These service-reported timestamps advance independently of a patch.
        props.get('backup', {}).pop('earliestRestoreDate', None)
        props.get('backup', {}).pop('backupRetentionDays', None)
        props.pop('state', None)
    return result

def diagnostic_path(name):
    return path('registry', name)[0] + '/providers/Microsoft.Insights/diagnosticSettings/' + DIAGNOSTIC + '?api-version=2021-05-01-preview'

def main(stage, name=None):
    if stage == 'plan':
        workspace = request('GET', WORKSPACE + '?api-version=2022-10-01')
        if workspace['location'].replace(' ', '').lower() != 'eastasia':
            raise ValueError('Audit logs must remain in East Asia')
        resources = {}
        for kind, names in (('registry', REGISTRIES), ('database', DATABASES)):
            for target in names:
                scope, api = path(kind, target)
                item = request('GET', scope + api)
                if item['location'].replace(' ', '').lower() != 'eastasia':
                    raise ValueError('Resource outside approved region')
                if kind == 'registry':
                    if item['properties']['provisioningState'] != 'Succeeded':
                        raise ValueError('Registry not stable')
                    if item['sku']['name'] not in ('Basic', 'Standard', 'Premium'):
                        raise ValueError('Unknown registry tier')
                    diagnostics = request('GET', scope + '/providers/Microsoft.Insights/diagnosticSettings?api-version=2021-05-01-preview')
                    if any(d['name'] == DIAGNOSTIC for d in diagnostics['value']):
                        raise ValueError('Target diagnostic name already exists')
                    categories = request('GET', scope + '/providers/Microsoft.Insights/diagnosticSettingsCategories?api-version=2021-05-01-preview')
                    if not {'ContainerRegistryLoginEvents', 'ContainerRegistryRepositoryEvents'} <= {d['name'] for d in categories['value']}:
                        raise ValueError('Required logging categories unavailable')
                    resources[target] = {'kind':kind, 'before':item, 'diagnostics_before':diagnostics}
                else:
                    if item['properties']['state'] != 'Ready':
                        raise ValueError('Database not ready')
                    resources[target] = {'kind':kind, 'before':item}
        plan = {'created_at':datetime.now(timezone.utc).isoformat(),
                'owner_decision':'Cost is not a constraint; keep all data in East Asia.',
                'scope':'ACR Premium prerequisites and registry audit logs; 35-day retention on two existing operational databases only.',
                'no_public_access_change':True, 'no_cross_region_copy':True,
                'no_ha_change':True, 'no_credential_change':True, 'resources':resources,
                'workspace':WORKSPACE, 'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        save('plan', plan)
        save('plan-protected-receipt', retain(plan, 'eastasia-resilience-plan'))
        print(json.dumps({'planned':list(resources), 'location':'eastasia',
                          'database_retention_days':{n:resources[n]['before']['properties']['backup']['backupRetentionDays'] for n in DATABASES}}))
        return
    plan = read('plan')
    from rehearse_ms_worker_image_rollback import restore_protected
    protected, _ = restore_protected(read('plan-protected-receipt'))
    if protected != plan or plan['controller_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError('Protected plan/controller differs')
    record = plan['resources'][name]
    kind = record['kind']
    scope, api = path(kind, name)
    current = request('GET', scope + api)
    before = record['before']
    if normalized(current, kind) != normalized(before, kind):
        raise ValueError('Unexpected resource configuration drift')
    if stage == 'apply':
        if kind == 'registry':
            if current['sku'] != before['sku'] or current['properties']['provisioningState'] != 'Succeeded':
                raise ValueError('Registry already changed or busy')
            payload = {'sku':{'name':'Premium'}}
        else:
            if current['properties']['backup']['backupRetentionDays'] != before['properties']['backup']['backupRetentionDays'] or current['properties']['state'] != 'Ready':
                raise ValueError('Database already changed or busy')
            payload = {'properties':{'backup':{'backupRetentionDays':35}}}
        save(name+'-intent', {'scope':scope, 'patch':payload, 'before':current,
                            'rollback_patch':{'sku':{'name':before['sku']['name']}} if kind=='registry' else {'properties':{'backup':{'backupRetentionDays':before['properties']['backup']['backupRetentionDays']}}}})
        response = request('PATCH', scope+api, data=payload)
        save(name+'-ack', {'id':response.get('id'), 'state':response.get('properties',{}).get('provisioningState',response.get('properties',{}).get('state'))})
        print(json.dumps({'resource':name, 'submitted':payload, 'recheck_required':True}))
    elif stage == 'verify':
        expected = current['sku']['name']=='Premium' if kind=='registry' else current['properties']['backup']['backupRetentionDays']==35
        state = current['properties'].get('provisioningState') if kind=='registry' else current['properties']['state']
        if not expected or state not in ('Ready','Succeeded'):
            raise ValueError('Change not completed; wait and recheck')
        proof={'resource':name, 'after':current, 'other_settings_preserved':True, 'verified_at':datetime.now(timezone.utc).isoformat()}
        save(name+'-verified',proof)
        save(name+'-verified-protected',retain(proof,'eastasia-resilience-verified'))
        print(json.dumps({'resource':name,'verified':True,'other_settings_preserved':True}))
    elif stage == 'logging':
        if kind!='registry' or current['sku']['name']!='Premium' or current['properties']['provisioningState']!='Succeeded':
            raise ValueError('Verified Premium registry required')
        read(name+'-verified')
        if request('GET',diagnostic_path(name),missing=True):
            raise ValueError('Diagnostic already exists; reconcile')
        payload={'properties':{'workspaceId':WORKSPACE,'logAnalyticsDestinationType':'Dedicated',
                              'logs':[{'category':c,'enabled':True} for c in ('ContainerRegistryLoginEvents','ContainerRegistryRepositoryEvents')]}}
        save(name+'-logging-intent',payload)
        request('PUT',diagnostic_path(name),data=payload)
        observed=request('GET',diagnostic_path(name))
        props=observed['properties']
        if props['workspaceId'].lower()!=WORKSPACE.lower() or props.get('logAnalyticsDestinationType')!='Dedicated' or {(d['category'],d['enabled']) for d in props['logs']}!={(d['category'],True) for d in payload['properties']['logs']}:
            raise ValueError('Logging verification failed')
        proof={'resource':name,'diagnostic':observed,'ingestion_verified':False}
        save(name+'-logging-verified',proof)
        save(name+'-logging-protected',retain(proof,'eastasia-registry-diagnostics'))
        print(json.dumps({'resource':name,'audit_logging_configured':True,'ingestion_verification_pending':True}))
    else:
        raise ValueError('Unknown stage')

if __name__ == '__main__':
    main(*sys.argv[1:])
