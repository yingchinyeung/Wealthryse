"""One new private PostgreSQL host and ONE new test credential; no SQL or agents.

This trusted owner-side provisioning controller is not an agent tool. Existing
resources are read only. A fixed protected reservation coordinates this exact
new target across cooperating controllers; it cannot fence an unrelated Azure
administrator. Azure server PUT and Key Vault set are not advertised as atomic
create-only APIs. Exact absence is rechecked immediately before each single PUT.
Uncertain replies consume the attempt: inspect, never regenerate or redispatch.
"""
import argparse
import hashlib
import json
import secrets
import time
import re
import sys
from datetime import datetime
from importlib.metadata import version
from urllib.parse import quote, urlsplit, parse_qs

import requests
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions

import deploy_owner_task_postgres_trial as deploy
import verify_owner_task_source_checkpoint as qa
from ibkr_etl.owner_codex_isolated_target import IsolatedTarget, validate_server_arm


TARGET = IsolatedTarget()
FOLDER = deploy.ROOT / '.work/owner-task-isolated-host-20261004'
VAULT = 'https://ibkrv2stg-auhecwc4itaoo.vault.azure.net'
ARM = 'https://management.azure.com'
API = '2024-08-01'
RESERVATION = 'probes/owner-codex-isolated-host-20261004/provision-reservation.json'
MAX_BYTES = 2 * 1024 * 1024
KINDS = {name: 'isolated-host-' + name.removesuffix('.json') for name in (
    'preflight.json', 'provision-intent.json', 'secret-intent.json', 'secret-ack.json',
    'server-intent.json', 'server-ack.json', 'host-acceptance.json')}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sources():
    return {k: v for k, v in qa.source_manifest().items() if k != qa.DOCUMENT}


def runtime_descriptor():
    # Compatibility context only; not installed-byte or vulnerability certification.
    return dict(python=sys.version.split()[0], versions={name: version(name) for name in
        ('azure-core', 'azure-storage-blob', 'azure-identity', 'requests')},
        requirements_lock_sha256=hashlib.sha256((deploy.ROOT / 'requirements.lock').read_bytes()).hexdigest())


def public_definition():
    return dict(location='eastasia', sku=dict(name='Standard_B1ms', tier='Burstable'),
        tags=dict(application='ibkr-portfolio-platform', purpose='owner-codex-isolated-trial',
            marker=TARGET.marker, profile=TARGET.profile_key, sourceScope=TARGET.physical_scope_sha256),
        properties=dict(createMode='Default', version='16', administratorLogin=TARGET.administrator_login,
            storage=dict(storageSizeGB=32, autoGrow='Disabled'),
            backup=dict(backupRetentionDays=7, geoRedundantBackup='Disabled'),
            highAvailability=dict(mode='Disabled'),
            authConfig=dict(passwordAuth='Enabled', activeDirectoryAuth='Disabled'),
            network=dict(publicNetworkAccess='Disabled', delegatedSubnetResourceId=TARGET.delegated_subnet_resource_id,
                privateDnsZoneArmResourceId=TARGET.private_dns_zone_resource_id)))


def checked_owner():
    owner = deploy.az('account', 'show', '--query', '{subscription:id,user:user.name,type:user.type}')
    if owner != dict(subscription=TARGET.subscription_id, user='anthony.ying@wealthryse.ai', type='user'):
        raise ValueError('isolated_host_registered_owner_required')


class Transport:
    """Two exact write endpoints; tokens scoped/cached, no HTTP retries/redirects."""
    def __init__(self, credential=None, session=None):
        self.credential = credential or AzureCliCredential()
        self.session = session or requests.Session()
        self.tokens = {}

    def call(self, method, url, *, body=None, missing=False):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.fragment or parsed.username or parsed.password
                or parsed.netloc not in ('management.azure.com', 'ibkrv2stg-auhecwc4itaoo.vault.azure.net')):
            raise ValueError('isolated_host_unregistered_endpoint')
        vault = parsed.netloc != 'management.azure.com'
        secret_path = '/secrets/' + TARGET.admin_secret_name
        if method == 'PUT':
            allowed = ((not vault and parsed.path == TARGET.server_resource_id and parsed.query == 'api-version=' + API)
                or (vault and parsed.path == secret_path and parsed.query == 'api-version=7.4'))
            if not allowed:
                raise ValueError('isolated_host_unregistered_write')
        elif method != 'GET' or body is not None:
            raise ValueError('isolated_host_method_not_allowed')
        else:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if vault:
                allowed_paths = ('/secrets', '/deletedsecrets', secret_path + '/versions')
                allowed = (parsed.path in allowed_paths and query.get('api-version') == ['7.4']
                    and not set(query) - {'api-version', 'maxresults', '$skiptoken'}
                    and query.get('maxresults', ['25']) == ['25']
                    and ('$skiptoken' not in query or len(query['$skiptoken']) == 1 and bool(query['$skiptoken'][0])))
            else:
                allowed_paths = {TARGET.server_resource_id: API, TARGET.delegated_subnet_resource_id: '2024-05-01',
                    TARGET.private_dns_zone_resource_id: '2020-06-01',
                    TARGET.private_dns_zone_resource_id + '/virtualNetworkLinks': '2020-06-01',
                    TARGET.environment_resource_id: '2024-03-01'}
                allowed = parsed.path in allowed_paths and parsed.query == 'api-version=' + allowed_paths[parsed.path]
            if not allowed:
                raise ValueError('isolated_host_unregistered_read')
        audience = 'https://vault.azure.net/.default' if vault else 'https://management.azure.com/.default'
        token = self.tokens.get(audience)
        if token is None or token.expires_on <= time.time() + 120:
            token = self.credential.get_token(audience)
            self.tokens[audience] = token
        try:
            response = self.session.request(method, url, json=body,
                headers={'Authorization': 'Bearer ' + token.token},
                timeout=(5, 20), allow_redirects=False, stream=True)
            try:
                status = response.status_code
                if missing and method == 'GET' and status == 404:
                    return None, status
                accepted_status = (200,) if method == 'GET' else (200, 201) if vault else (200, 201, 202)
                if status not in accepted_status:
                    raise RuntimeError('isolated_host_http_result_unaccepted')
                chunks, count = [], 0
                for chunk in response.iter_content(chunk_size=16384):
                    count += len(chunk)
                    if count > MAX_BYTES:
                        raise RuntimeError('isolated_host_http_result_unaccepted')
                    chunks.append(chunk)
                payload = b''.join(chunks)
                value = json.loads(payload) if payload else {}
                if type(value) is not dict:
                    raise ValueError('isolated_host_http_shape_invalid')
                return value, status
            finally:
                response.close()
        except Exception:
            # Never include a provider body, request data, token or credential.
            raise RuntimeError('isolated_host_http_outcome_requires_inspection') from None


def arm_get(client, path, version, *, missing=False):
    return client.call('GET', ARM + path + '?api-version=' + version, missing=missing)[0]


def secret_inventory(client, category='secrets'):
    if category not in ('secrets', 'deletedsecrets', 'secrets/' + TARGET.admin_secret_name + '/versions'):
        raise ValueError('isolated_host_secret_metadata_scope_invalid')
    path = '/' + category
    url = VAULT + path + '?api-version=7.4&maxresults=25'
    seen, ids = set(), []
    for _ in range(100):
        if url in seen:
            raise ValueError('isolated_host_repeated_secret_metadata_page')
        seen.add(url)
        page, _ = client.call('GET', url, missing=category.endswith('/versions'))
        if page is None and category.endswith('/versions'):
            return []
        if type(page.get('value')) is not list or len(page['value']) > 25:
            raise ValueError('isolated_host_secret_metadata_shape_invalid')
        for row in page['value']:
            item = row.get('id') if type(row) is dict else None
            target = urlsplit(item) if type(item) is str else None
            if (target is None or target.scheme != 'https' or target.netloc != urlsplit(VAULT).netloc
                    or target.query or target.fragment or target.username or target.password
                    or not re.fullmatch(r'/secrets/[A-Za-z0-9-]{1,127}(?:/[0-9a-f]{32})?', target.path)
                    or item in ids):
                raise ValueError('isolated_host_secret_metadata_id_invalid')
            if category.endswith('/versions') and (not target.path.startswith('/secrets/' + TARGET.admin_secret_name + '/')
                    or len(target.path.split('/')) != 4):
                raise ValueError('isolated_host_secret_version_scope_invalid')
            ids.append(item)
        link = page.get('nextLink')
        if link in (None, ''):
            return ids
        if type(link) is not str:
            raise ValueError('isolated_host_secret_continuation_invalid')
        continuation = urlsplit(link)
        query = parse_qs(continuation.query, keep_blank_values=True)
        if (continuation.scheme != 'https' or continuation.netloc not in (urlsplit(VAULT).netloc, urlsplit(VAULT).netloc + ':443')
                or continuation.path != path or continuation.fragment or continuation.username or continuation.password
                or query.get('api-version') != ['7.4'] or set(query) - {'api-version', 'maxresults', '$skiptoken'}
                or query.get('maxresults', ['25']) != ['25'] or len(query.get('$skiptoken', [])) != 1
                or not query['$skiptoken'][0]):
            raise ValueError('isolated_host_secret_continuation_invalid')
        # Azure emits the explicit default HTTPS port on deleted-secret pages.
        # Canonicalize only this exact authority; never follow another port,
        # path, host, credentials or redirect, and retain strict GET allowlists.
        url = continuation._replace(netloc=urlsplit(VAULT).netloc).geturl()
    raise ValueError('isolated_host_secret_inventory_incomplete')


def require_secret_absent(client):
    for category in ('secrets', 'deletedsecrets'):
        if any(urlsplit(item).path.split('/')[2].casefold() == TARGET.admin_secret_name.casefold()
                for item in secret_inventory(client, category)):
            raise ValueError('isolated_host_new_secret_name_already_reserved')


def require_server_absent(client):
    if arm_get(client, TARGET.server_resource_id, API, missing=True) is not None:
        raise ValueError('isolated_host_server_already_exists_inspect_only')


def dependencies(client):
    subnet = arm_get(client, TARGET.delegated_subnet_resource_id, '2024-05-01')
    dns = arm_get(client, TARGET.private_dns_zone_resource_id, '2020-06-01')
    environment = arm_get(client, TARGET.environment_resource_id, '2024-03-01')
    links = arm_get(client, TARGET.private_dns_zone_resource_id + '/virtualNetworkLinks', '2020-06-01')
    vnet = TARGET.delegated_subnet_resource_id.rsplit('/subnets/', 1)[0]
    if (subnet.get('id') != TARGET.delegated_subnet_resource_id
            or subnet.get('properties', {}).get('provisioningState') != 'Succeeded'
            or [d.get('properties', {}).get('serviceName') for d in subnet['properties'].get('delegations', [])]
                != ['Microsoft.DBforPostgreSQL/flexibleServers']
            or dns.get('id') != TARGET.private_dns_zone_resource_id
            or environment.get('id') != TARGET.environment_resource_id
            or environment.get('properties', {}).get('provisioningState') != 'Succeeded'
            or environment['properties'].get('vnetConfiguration', {}).get('infrastructureSubnetId')
                != vnet + '/subnets/container-apps'
            or links.get('nextLink') not in (None, '') or type(links.get('value')) is not list
            or any(type(link) is not dict or type(link.get('properties')) is not dict for link in links['value'])
            or not any(link.get('properties', {}).get('virtualNetwork', {}).get('id') == vnet
                and link['properties'].get('virtualNetworkLinkState') == 'Completed'
                and link['properties'].get('provisioningState') == 'Succeeded'
                and link['properties'].get('registrationEnabled') is False for link in links['value'])):
        raise ValueError('isolated_host_private_dependencies_unverified')
    # Provisioning can allocate DNS/IP metadata. Freeze stable configuration,
    # not mutable allocation fields, and explicitly do not claim zero network delta.
    return dict(subnet_id=TARGET.delegated_subnet_resource_id, address_prefix=subnet['properties'].get('addressPrefix'),
        delegation='Microsoft.DBforPostgreSQL/flexibleServers',
        nsg_sha256=sha(subnet['properties'].get('networkSecurityGroup')),
        route_table_sha256=sha(subnet['properties'].get('routeTable')),
        dns_id=TARGET.private_dns_zone_resource_id, linked_vnet_id=vnet,
        environment_id=TARGET.environment_resource_id,
        environment_network_sha256=sha(environment['properties']['vnetConfiguration']))


def save(value, name):
    deploy.FOLDER = FOLDER
    return deploy.save(value, name, KINDS[name])


def load(name):
    raw = json.loads((FOLDER / name).read_text(encoding='utf-8'))
    return raw, deploy.protected(raw, KINDS[name])


def reservation_blob(version=None):
    deploy.archive_protection()
    service = BlobServiceClient('https://' + deploy.ACCOUNT + '.blob.core.windows.net',
        credential=AzureCliCredential(), connection_timeout=5, read_timeout=10,
        retry_total=0, retry_to_secondary=False, permit_redirects=False, redirect_max=0,
        max_single_put_size=MAX_BYTES)
    return service.get_blob_client(deploy.CONTAINER, RESERVATION, version_id=version)


def reservation_readback(receipt, payload):
    if (type(receipt) is not dict or set(receipt) != {'blob', 'sha256', 'version_id', 'etag', 'protected_readback_verified'}
            or receipt.get('blob') != RESERVATION
            or receipt.get('protected_readback_verified') is not True
            or receipt.get('sha256') != hashlib.sha256(payload).hexdigest()
            or type(receipt.get('version_id')) is not str
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt['version_id']) is None
            or type(receipt.get('etag')) is not str or re.fullmatch(r'"0x[0-9A-Fa-f]+"', receipt['etag']) is None
            or not 0 < len(payload) <= MAX_BYTES):
        raise ValueError('isolated_host_reservation_receipt_invalid')
    datetime.fromisoformat(receipt['version_id'].replace('Z', '+00:00'))
    # Fresh pinned client: a current version never substitutes for this ACK.
    pinned = reservation_blob(receipt['version_id'])
    props = pinned.get_blob_properties(timeout=10, retry_total=0)
    if (props.size != len(payload) or props.version_id != receipt['version_id']
            or props.etag != receipt['etag'] or props.blob_type != 'BlockBlob'
            or pinned.download_blob(offset=0, length=len(payload)+1, etag=props.etag,
                match_condition=MatchConditions.IfNotModified, max_concurrency=1, timeout=10,
                retry_total=0).readall() != payload):
        raise ValueError('isolated_host_reservation_ack_unverified')
    deploy.archive_protection()


def reserve(value):
    """Never treats existing equal bytes or a lost ACK as create authority."""
    blob = reservation_blob()
    payload = canonical(value)
    if not 0 < len(payload) <= MAX_BYTES:
        raise ValueError('isolated_host_reservation_size_invalid')
    ack = blob.upload_blob(payload, length=len(payload), blob_type='BlockBlob', overwrite=False,
        match_condition=MatchConditions.IfMissing, max_concurrency=1, timeout=10, retry_total=0)
    receipt = dict(blob=RESERVATION, sha256=hashlib.sha256(payload).hexdigest(), version_id=ack.get('version_id'),
        etag=ack.get('etag'), protected_readback_verified=True)
    reservation_readback(receipt, payload)
    return receipt


def still_frozen(prepared, client):
    if (sources() != prepared['sources'] or runtime_descriptor() != prepared['runtime']
            or deploy.baseline() != prepared['baseline'] or dependencies(client) != prepared['dependencies']):
        raise ValueError('isolated_host_source_or_existing_configuration_drift')


def verify_prepared(prepared):
    if (prepared.get('status') != 'prepared_new_host_no_sql' or prepared.get('sources') != sources()
            or prepared.get('runtime') != runtime_descriptor()
            or prepared.get('definition') != public_definition() or prepared.get('registration_sha256') != TARGET.registration_sha256
            or prepared.get('physical_scope_sha256') != TARGET.physical_scope_sha256
            or prepared.get('baseline', {}).get('job_inventory_complete') is not True
            or len(prepared['baseline'].get('other_jobs', {})) != 34):
        raise ValueError('isolated_host_exact_protected_preflight_required')


def provision(prepared_raw, prepared, client):
    for name in ('provision-intent.json', 'secret-intent.json', 'secret-ack.json', 'server-intent.json', 'server-ack.json'):
        if (FOLDER / name).exists():
            raise ValueError('isolated_host_attempt_consumed_inspect_no_redispatch')
    verify_prepared(prepared)
    still_frozen(prepared, client)
    require_secret_absent(client)
    require_server_absent(client)
    intent = save(dict(status='single_new_target_attempt', sources=prepared['sources'], definition=public_definition(),
        registration_sha256=TARGET.registration_sha256, physical_scope_sha256=TARGET.physical_scope_sha256,
        preflight_receipt=prepared_raw['protected_receipt'], automatic_retry=False,
        exclusive_cooperating_controller_scope=True, arbitrary_administrator_race_containment=False,
        sql_calls=0, model_calls=0, production_credentials_used=False), 'provision-intent.json')
    deploy.protected(intent, KINDS['provision-intent.json'])
    reservation = reserve({k: v for k, v in intent.items() if k != 'protected_receipt'})
    still_frozen(prepared, client)
    require_server_absent(client)
    require_secret_absent(client)
    secret_intent = save(dict(status='new_secret_single_put', provision_receipt=intent['protected_receipt'],
        reservation=reservation, secret_name=TARGET.admin_secret_name,
        server_resource_id=TARGET.server_resource_id, credential_value_retained=False, automatic_retry=False), 'secret-intent.json')
    deploy.protected(secret_intent, KINDS['secret-intent.json'])
    if sources() != prepared['sources']:
        raise ValueError('isolated_host_source_changed_before_secret_put')
    require_secret_absent(client)
    password = 'Aa9!' + secrets.token_urlsafe(48)
    url = 'postgresql://' + TARGET.administrator_login + ':' + quote(password, safe='') + '@' + TARGET.host
    url += ':5432/postgres?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fcerts%2Fca-certificates.crt'
    reply, status = client.call('PUT', VAULT + '/secrets/' + TARGET.admin_secret_name + '?api-version=7.4',
        body=dict(value=url, contentType='application/x-postgresql-test-dsn',
            tags=dict(purpose='owner-codex-isolated-trial', marker=TARGET.marker,
                intentSha256=secret_intent['protected_receipt']['sha256'])))
    expected_prefix = VAULT + '/secrets/' + TARGET.admin_secret_name + '/'
    secret_id = reply.get('id')
    if (type(secret_id) is not str or not secret_id.startswith(expected_prefix)
            or len(secret_id[len(expected_prefix):]) != 32
            or any(c not in '0123456789abcdef' for c in secret_id[len(expected_prefix):])
            or reply.get('value') != url):
        raise ValueError('isolated_host_new_secret_ack_unverified')
    reply.clear()
    url = None
    secret_ack = save(dict(status='new_secret_acknowledged', secret_version_id=secret_id,
        http_status=status, secret_intent_receipt=secret_intent['protected_receipt'], credentials_retained=False), 'secret-ack.json')
    deploy.protected(secret_ack, KINDS['secret-ack.json'])
    still_frozen(prepared, client)
    require_server_absent(client)
    versions = secret_inventory(client, 'secrets/' + TARGET.admin_secret_name + '/versions')
    if versions != [secret_id]:
        raise ValueError('isolated_host_new_secret_version_drift')
    server_intent = save(dict(status='new_server_single_put', definition=public_definition(),
        provision_receipt=intent['protected_receipt'], secret_ack_receipt=secret_ack['protected_receipt'],
        sources=prepared['sources'], automatic_retry=False, source_or_restore_inputs=False), 'server-intent.json')
    deploy.protected(server_intent, KINDS['server-intent.json'])
    if sources() != prepared['sources']:
        raise ValueError('isolated_host_source_changed_before_server_put')
    require_server_absent(client)
    body = public_definition()
    body['properties']['administratorLoginPassword'] = password
    try:
        reply, status = client.call('PUT', ARM + TARGET.server_resource_id + '?api-version=' + API, body=body)
    finally:
        body['properties'].pop('administratorLoginPassword', None)
        password = None
    reply.clear()  # Acceptance is a separate authenticated GET, not this ACK.
    ack = save(dict(status='new_server_request_acknowledged', server_resource_id=TARGET.server_resource_id,
        http_status=status, server_intent_receipt=server_intent['protected_receipt'],
        definition_sha256=sha(public_definition()), sql_acceptance=False), 'server-ack.json')
    deploy.protected(ack, KINDS['server-ack.json'])
    return dict(status=ack['status'], server_name=TARGET.server_name, protected_receipt=ack['protected_receipt'],
        sql_acceptance=False, owner_runtime_active=False, six_specialist_runtimes_active=False)


def observe(client):
    server = arm_get(client, TARGET.server_resource_id, API, missing=True)
    result = dict(status='isolated_host_read_only_observation', server_exists=server is not None,
        server=None if server is None else validate_server_arm(server, require_ready=False),
        new_secret_versions=secret_inventory(client, 'secrets/' + TARGET.admin_secret_name + '/versions'),
        redispatch_authorized=False, sql_acceptance=False, model_calls=0)
    if server is not None and any(server.get('tags', {}).get(k) != v for k, v in public_definition()['tags'].items()):
        raise ValueError('isolated_host_registration_tags_mismatch')
    return result


def validate_chain(prepared_raw, prepared, records):
    """Protected bytes alone do not establish correct action scope or provenance."""
    def value(name):
        raw = records[name]
        return {k: v for k, v in raw.items() if k != 'protected_receipt'}

    intent = value('provision-intent.json')
    wanted = dict(status='single_new_target_attempt', sources=prepared['sources'], definition=public_definition(),
        registration_sha256=TARGET.registration_sha256, physical_scope_sha256=TARGET.physical_scope_sha256,
        preflight_receipt=prepared_raw['protected_receipt'], automatic_retry=False,
        exclusive_cooperating_controller_scope=True, arbitrary_administrator_race_containment=False,
        sql_calls=0, model_calls=0, production_credentials_used=False)
    if canonical(intent) != canonical(wanted):
        raise ValueError('isolated_host_provision_intent_contract_invalid')
    secret_intent = value('secret-intent.json')
    reservation = secret_intent.get('reservation')
    wanted = dict(status='new_secret_single_put', provision_receipt=records['provision-intent.json']['protected_receipt'],
        reservation=reservation, secret_name=TARGET.admin_secret_name,
        server_resource_id=TARGET.server_resource_id, credential_value_retained=False, automatic_retry=False)
    if canonical(secret_intent) != canonical(wanted):
        raise ValueError('isolated_host_secret_intent_contract_invalid')
    secret_ack = value('secret-ack.json')
    version_id, secret_status = secret_ack.get('secret_version_id'), secret_ack.get('http_status')
    if (type(version_id) is not str or re.fullmatch(re.escape(VAULT + '/secrets/' + TARGET.admin_secret_name + '/')
            + r'[0-9a-f]{32}', version_id) is None or type(secret_status) is not int or secret_status not in (200, 201)):
        raise ValueError('isolated_host_secret_ack_scope_invalid')
    wanted = dict(status='new_secret_acknowledged', secret_version_id=version_id, http_status=secret_status,
        secret_intent_receipt=records['secret-intent.json']['protected_receipt'], credentials_retained=False)
    if canonical(secret_ack) != canonical(wanted):
        raise ValueError('isolated_host_secret_ack_contract_invalid')
    wanted = dict(status='new_server_single_put', definition=public_definition(),
        provision_receipt=records['provision-intent.json']['protected_receipt'],
        secret_ack_receipt=records['secret-ack.json']['protected_receipt'], sources=prepared['sources'],
        automatic_retry=False, source_or_restore_inputs=False)
    if canonical(value('server-intent.json')) != canonical(wanted):
        raise ValueError('isolated_host_server_intent_contract_invalid')
    server_ack = value('server-ack.json')
    server_status = server_ack.get('http_status')
    if type(server_status) is not int or server_status not in (200, 201, 202):
        raise ValueError('isolated_host_server_ack_status_invalid')
    wanted = dict(status='new_server_request_acknowledged', server_resource_id=TARGET.server_resource_id,
        http_status=server_status, server_intent_receipt=records['server-intent.json']['protected_receipt'],
        definition_sha256=sha(public_definition()), sql_acceptance=False)
    if canonical(server_ack) != canonical(wanted):
        raise ValueError('isolated_host_server_ack_contract_invalid')
    reservation_readback(reservation, canonical(intent))
    return intent, secret_ack


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'provision', 'inspect', 'verify'))
    args = parser.parse_args()
    checked_owner()
    deploy.FOLDER = FOLDER
    FOLDER.mkdir(parents=True, exist_ok=True)
    client = Transport()
    if args.stage == 'prepare':
        if (FOLDER / 'preflight.json').exists():
            raise ValueError('isolated_host_preflight_reserved_no_overwrite')
        before = sources()
        runtime = runtime_descriptor()
        local = qa.run_local_checks()
        if sources() != before or runtime_descriptor() != runtime:
            raise ValueError('isolated_host_source_changed_during_qa')
        baseline = deploy.baseline()
        if baseline.get('job_inventory_complete') is not True or len(baseline.get('other_jobs', {})) != 34:
            raise ValueError('isolated_host_complete_existing_inventory_required')
        network = dependencies(client)
        require_secret_absent(client)
        require_server_absent(client)
        if sources() != before or runtime_descriptor() != runtime:
            raise ValueError('isolated_host_source_changed_during_preflight')
        raw = save(dict(status='prepared_new_host_no_sql', sources=before, runtime=runtime, definition=public_definition(),
            local_checks=local, baseline=baseline, dependencies=network,
            registration_sha256=TARGET.registration_sha256, physical_scope_sha256=TARGET.physical_scope_sha256,
            existing_target_absence_observed=True, sql_calls=0, model_calls=0,
            catalog_worker_deployed=False, new_identity_or_rbac=False,
            automatic_retry=False), 'preflight.json')
        deploy.protected(raw, KINDS['preflight.json'])
        print(json.dumps(dict(status=raw['status'], local_checks=local, protected_receipt=raw['protected_receipt'])), flush=True)
        return
    if args.stage == 'inspect':
        print(json.dumps(observe(client), sort_keys=True), flush=True)
        return
    prepared_raw, prepared = load('preflight.json')
    verify_prepared(prepared)
    if args.stage == 'provision':
        print(json.dumps(provision(prepared_raw, prepared, client), sort_keys=True), flush=True)
        return
    # Never infer completion from a dispatch ACK or reconstruct write authority.
    records = {name: load(name)[0] for name in ('provision-intent.json', 'secret-intent.json', 'secret-ack.json',
        'server-intent.json', 'server-ack.json')}
    intent, secret_ack = validate_chain(prepared_raw, prepared, records)
    secret_raw, server_raw, intent_raw = (records[name] for name in ('secret-ack.json', 'server-ack.json', 'provision-intent.json'))
    observed = observe(client)
    if (not observed.get('server') or observed['server'].get('ready_observed') is not True
            or observed['new_secret_versions'] != [secret_ack.get('secret_version_id')]):
        raise ValueError('isolated_host_not_ready_or_secret_drift_inspect_only')
    still_frozen(prepared, client)
    accepted = dict(status='new_private_host_verified_sql_pending', observation=observed,
        sources=prepared['sources'], runtime=prepared['runtime'], preflight_receipt=prepared_raw['protected_receipt'],
        provision_intent_receipt=intent_raw['protected_receipt'], server_ack_receipt=server_raw['protected_receipt'],
        secret_ack_receipt=secret_raw['protected_receipt'], original_default_request_verified=True,
        existing_jobs_unchanged=34, baseline_unchanged=True, new_identity_or_rbac=False,
        incidental_private_dns_or_ip_allocation_expected=True,
        blank_sql_inventory_verified=False, fresh_restricted_login_verified=False,
        production_migration_059_installed=False, owner_runtime_active=False,
        six_specialist_runtimes_active=False, ibkr_source_writes=False, model_calls=0)
    if (FOLDER / 'host-acceptance.json').exists():
        _, previous = load('host-acceptance.json')
        if previous != accepted:
            raise ValueError('isolated_host_independent_readback_differs')
        print(json.dumps(dict(status='independent_host_readback_verified', owner_runtime_active=False,
            fresh_restricted_login_verified=False)), flush=True)
    else:
        raw = save(accepted, 'host-acceptance.json')
        deploy.protected(raw, KINDS['host-acceptance.json'])
        print(json.dumps(dict(status=accepted['status'], protected_receipt=raw['protected_receipt'],
            owner_runtime_active=False, fresh_restricted_login_verified=False)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Fixed public message: never render raw cloud exceptions or credentials.
        print(json.dumps(dict(status='not_accepted_inspect_before_further_action',
            automatic_retry=False, sql_acceptance=False)), flush=True)
        raise SystemExit(1) from None
