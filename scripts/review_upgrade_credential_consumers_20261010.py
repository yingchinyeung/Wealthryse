"""Read-only ownership/rotation preparation; no secret contents or mutations.

Container references are not a complete external-caller proof. In particular,
unreferenced credentials must not be retired on this inventory alone.
"""
from datetime import datetime,timezone
import json
from urllib.parse import urlsplit,unquote
import control_upgrade_report_admin_20261010 as c


def main():
    observation=c.control.protected(c.ROOT/'.work/platform-upgrade-20261010-stage22/secret-metadata-v1/scheduled-observation-v1/result.json')
    resources=c.current_resources();before=c.bindings(resources);references={};inline_names=[]
    for item in resources:
        p=item['properties'];config=p.get('configuration') or {}
        for secret in config.get('secrets') or []:
            raw=secret.get('keyVaultUrl')
            if not raw:
                if secret.get('value'):inline_names.append({'workload':item['name'],'alias':secret['name'],'value_omitted':True})
                continue
            url=urlsplit(raw);parts=[unquote(x) for x in url.path.split('/') if x]
            if url.scheme!='https' or not url.hostname or not url.hostname.endswith('.vault.azure.net') or len(parts) not in (2,3) or parts[0]!='secrets' or url.query or url.fragment:
                raise ValueError('Unexpected vault reference shape')
            key=(url.hostname.removesuffix('.vault.azure.net'),parts[1])
            env=[]
            for container in p['template']['containers']:
                env.extend(e['name'] for e in container.get('env') or [] if e.get('secretRef')==secret['name'])
            references.setdefault(key,[]).append({'workload':item['name'],'alias':secret['name'],'environment_variables':sorted(env),
                'version_pinned':len(parts)==3,'identity':secret.get('identity'),'trigger':config.get('triggerType','Application')})
    rows=[]
    for vault in observation['proof']['vaults']:
        for finding in vault['findings']:
            name=finding['secret_name'];kind=('configuration_identifier' if name=='ibkr-flex-query-id' else
                'broker_token' if name=='ibkr-flex-token' else 'database_connection' if name.endswith('-url') else
                'database_password' if name.endswith('-password') else 'unclassified')
            rows.append({'vault':vault['vault'],'name':name,'classification_inferred_from_name_only':kind,
                'metadata_findings':finding['reasons'],'metadata_age_days':finding.get('age_days'),
                'container_references':references.get((vault['vault'],name),[]),
                'owner_confirmed':False,'actual_credential_expiry_verified':False,'rotation_dependency_group_verified':False,
                'external_or_build_callers_cleared':False,'retirement_authorized':False})
    if c.bindings(c.current_resources())!=before:raise ValueError('Workload bindings changed during review')
    record={'checked_at':datetime.now(timezone.utc).isoformat(),'metadata_observation_receipt':observation['protected_receipt'],
        'workload_count':len(resources),'secrets':rows,'inline_secret_reference_names_only':inline_names,
        'unreferenced_in_current_containers':[r['vault']+'/'+r['name'] for r in rows if not r['container_references']],
        'important_distinctions':['A query identifier is configuration, not an expiring credential; classification remains metadata-only.',
            'Multiple URLs and password entries may share one PostgreSQL role; names alone do not establish their rotation grouping.',
            'Missing tags do not establish that a secret is expired, leaked or unused.',
            'Container configuration does not inventory private build scripts, operator tools, external clients or dynamic broker consumers.'],
        'required_before_rotation':['Verify actual principal/role and every coupled URL/password without logging contents.',
            'Establish ownership and provider expiry using authoritative metadata.',
            'Rehearse recovery and client reconnection; retain a bounded rollback path.',
            'Verify each consumer after any version transition; never add arbitrary expiry to an active credential.'],
        'secret_values_read':0,'secret_values_changed':0,'permissions_changed':False,'rotation_complete':False,'full_upgrade_complete':False}
    c.control.save(c.OUT/'credential-consumers-v1','review',record)
    print(json.dumps({'metadata_records_reviewed':len(rows),'workloads':len(resources),
        'unreferenced_in_container_configuration':len(record['unreferenced_in_current_containers']),
        'secret_values_read':0,'retirement_or_rotation_performed':False}))


if __name__=='__main__':main()
