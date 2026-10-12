"""Additive catalog-only launch and native metadata compatibility.

The frozen SELECT worker and version-pinned test-host secret are unchanged.
Identity lifecycle None is configuration, not behavioral isolation proof.
Native inputs are preserved; only validated descriptive defaults are removed
from detached predecessor-validation shadows. No cloud or SQL effects exist.
"""
import ipaddress
import re

import owner_task_provider_launch_contract as prior

canonical, digest, require = prior.canonical, prior.digest, prior.require
registry = prior.registry


def copy(value):
    import json
    return json.loads(canonical(value))


def build(source_bytes, hashes, runtime):
    result = prior.build(source_bytes, hashes, runtime)
    result['definition']['properties']['configuration']['identitySettings'] = [
        dict(identity=registry.IDENTITY_ID,lifecycle='None')]
    result['definition_sha256'] = digest(result['definition'])
    return result


def template_shadow(value):
    shadow = copy(value)
    require(type(shadow) is dict and type(shadow.get('containers')) is list, 'launch_readback_invalid')
    for container in shadow['containers']:
        require(type(container) is dict,'launch_readback_invalid')
        if 'imageType' in container:
            require(container['imageType']=='ContainerImage','launch_readback_invalid')
            del container['imageType']
    return shadow


def validate_template(value,source_bytes,hashes,runtime,*,execution=False):
    return prior.validate_template(template_shadow(value),source_bytes,hashes,runtime,execution=execution)


def validate_job(value,source_bytes,hashes,runtime,*,principal_id,client_id):
    raw=copy(value);shadow=copy(raw);props=shadow.get('properties');require(type(props) is dict,'launch_readback_invalid')
    config=props.get('configuration');require(type(config) is dict,'launch_readback_invalid')
    allowed_ids=(registry.IDENTITY_ID,registry.IDENTITY_ID.replace('/resourceGroups/','/resourcegroups/'))
    identity=shadow.get('identity');require(type(identity) is dict,'launch_identity_invalid')
    assigned=identity.get('userAssignedIdentities');require(type(assigned) is dict and len(assigned)==1
        and next(iter(assigned)) in allowed_ids,'launch_identity_invalid')
    if registry.IDENTITY_ID not in assigned:
        identity['userAssignedIdentities']={registry.IDENTITY_ID:assigned[allowed_ids[1]]}
    settings=config.pop('identitySettings',None)
    require(type(settings) is list and len(settings)==1 and type(settings[0]) is dict
        and set(settings[0])=={'identity','lifecycle'} and settings[0]['identity'] in allowed_ids
        and settings[0]['lifecycle']=='None','launch_identity_invalid')
    if 'dapr' in config:
        require(config['dapr'] is None,'launch_readback_invalid');del config['dapr']
    registries=config.get('registries');require(type(registries) is list and len(registries)==1,'launch_readback_invalid')
    require(type(registries[0]) is dict and registries[0].get('identity') in allowed_ids,'launch_identity_invalid')
    registries[0]['identity']=registry.IDENTITY_ID
    secrets=config.get('secrets');require(type(secrets) is list and len(secrets)==1 and type(secrets[0]) is dict
        and secrets[0].get('identity') in allowed_ids,'launch_identity_invalid')
    secrets[0]['identity']=registry.IDENTITY_ID
    for key in ('username','passwordSecretRef'):
        if key in registries[0]:
            require(type(registries[0][key]) is str and registries[0][key]=='','launch_readback_invalid')
            del registries[0][key]
    if 'eventStreamEndpoint' in props:
        wanted=('https://eastasia.azurecontainerapps.dev/subscriptions/'+registry.SUBSCRIPTION
            +'/resourceGroups/'+registry.RESOURCE_GROUP+'/containerApps/'+registry.JOB+'/eventstream')
        require(props['eventStreamEndpoint']==wanted,'launch_readback_invalid');del props['eventStreamEndpoint']
    if 'outboundIpAddresses' in props:
        addresses=props['outboundIpAddresses'];require(type(addresses) is list and 0<len(addresses)<=512,'launch_readback_invalid')
        require(all(type(address) is str and str(ipaddress.IPv4Address(address))==address for address in addresses),
            'launch_readback_invalid')
    data=shadow.get('systemData')
    if data is not None:
        require(type(data) is dict and set(data)<= {'createdAt','lastModifiedAt','createdBy','lastModifiedBy','createdByType','lastModifiedByType'},
            'launch_readback_invalid')
        for key,item in data.items():
            require(type(item) is str and len(item)<=512 and all(c.isprintable() for c in item),'launch_readback_invalid')
            if key.endswith('At'):
                require(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}',item) is not None,
                    'launch_readback_invalid')
                from datetime import datetime
                datetime.fromisoformat(item) # Unspecified provider timezone, never asserted UTC.
            elif key.endswith('Type'): require(item in ('User','Application','ManagedIdentity','Key'),'launch_readback_invalid')
    props['template']=template_shadow(props.get('template'))
    prior.validate_job(shadow,source_bytes,hashes,runtime,principal_id=principal_id,client_id=client_id)
    return dict(native_job_sha256=digest(raw),definition_sha256=build(source_bytes,hashes,runtime)['definition_sha256'],
        identity_runtime_access_configured=False,identity_runtime_unavailability_verified=False,
        actual_execution_verified=False,provider_semantics_verified=False,restricted_login_verified=False)
