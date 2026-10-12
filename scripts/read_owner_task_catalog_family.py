"""Observed execution-family protocol gate, and one read-only canary rehearsal.

No import-time I/O, SQL, secret discovery, model access or job dispatch. The
family inventory is deliberately broader than the captured replica: unexpected
protocol rows are rejected, not filtered away. Two complete inventories enclose
the frozen full-payload reader. This establishes observed protocol completeness,
NOT absence of zero-log replicas, platform replacement or future late delivery.

The CLI only revisits the already-consumed synthetic execution. It can add
create-only protected evidence; it cannot provision or START a worker.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import time

import requests
from requests.adapters import HTTPAdapter

import read_owner_task_catalog_logs as reader
from ibkr_etl import owner_codex_catalog_transport as codec


def require(condition, code):
    reader._require(condition, code)


@dataclass(frozen=True)
class CatalogFamilyScope:
    replica_scope: reader.CatalogLogScope
    execution_name: str

    def __post_init__(self):
        require(type(self) is CatalogFamilyScope, 'logs_scope_invalid')
        replica = reader._scope(self.replica_scope)
        job = replica.binding.job_resource_id.rsplit('/', 1)[-1]
        require(type(self.execution_name) is str and re.fullmatch(
            re.escape(job) + r'-[a-z0-9]{7,12}', self.execution_name) is not None,
            'logs_scope_invalid')
        prefix = self.execution_name + '-'
        require(replica.log_group.startswith(prefix) and len(replica.log_group) > len(prefix),
            'logs_scope_invalid')


def checked_scope(value):
    require(type(value) is CatalogFamilyScope, 'logs_scope_invalid')
    value.__post_init__()
    return CatalogFamilyScope(reader._scope(value.replica_scope), value.execution_name)


@dataclass(frozen=True)
class CatalogFamilyQuery:
    scope: CatalogFamilyScope

    def __post_init__(self):
        require(type(self) is CatalogFamilyQuery, 'logs_query_invalid')
        checked_scope(self.scope)

    @property
    def text(self):
        self.__post_init__()
        scope = checked_scope(self.scope)
        # Only the exact execution prefix replaces the frozen inventory's
        # captured-group predicate. Never add a container/binding/version filter.
        inventory = reader.CatalogQuery(scope.replica_scope, 'inventory').text
        old = "ContainerGroupName_s == '" + scope.replica_scope.log_group + "'"
        new = "ContainerGroupName_s startswith '" + scope.execution_name + "-'"
        require(inventory.count(old) == 1, 'logs_query_invalid')
        return inventory.replace(old, new)


def validate_response(value, query, *, http_status=200):
    require(type(query) is CatalogFamilyQuery, 'logs_query_invalid')
    query.__post_init__()
    # This alias is for exact schema validation only. It does not issue a
    # narrower query or discard any row from the broad family response.
    alias = reader.CatalogQuery(checked_scope(query.scope).replica_scope, 'inventory')
    return reader.validate_response(value, alias, http_status=http_status)


class FamilyLogsTransport:
    def __init__(self, credential, session=None):
        self.credential = credential
        self.session = requests.Session() if session is None else session
        if session is None:
            self.session.mount('https://', HTTPAdapter(max_retries=0))
        self.token = None

    def __call__(self, query):
        require(type(query) is CatalogFamilyQuery, 'logs_query_invalid')
        query.__post_init__()
        scope = checked_scope(query.scope).replica_scope
        try:
            if self.token is None or self.token.expires_on <= time.time() + 120:
                self.token = self.credential.get_token(reader.AUDIENCE)
            response = self.session.request('POST', reader.ENDPOINT,
                json={'query': query.text, 'timespan': scope.start_utc + '/' + scope.end_utc},
                headers={'Authorization': 'Bearer ' + self.token.token, 'Content-Type': 'application/json'},
                allow_redirects=False, stream=True, timeout=(5, 20))
            try:
                require(type(response.status_code) is int and response.status_code == 200, 'logs_response_invalid')
                chunks, count = [], 0
                for chunk in response.iter_content(chunk_size=16384):
                    require(type(chunk) is bytes, 'logs_response_invalid')
                    count += len(chunk)
                    require(count <= reader.MAX_RESPONSE_BYTES, 'logs_response_oversized')
                    chunks.append(chunk)
                value = reader._json(b''.join(chunks), 'logs_response_invalid')
                validate_response(value, query, http_status=response.status_code)
                return value
            finally:
                response.close()
        except Exception:
            raise reader.CatalogLogsError('logs_transport_failed') from None


def family_inventory(query, scope):
    scope = checked_scope(scope)
    request = CatalogFamilyQuery(scope)
    response = query(request)
    validate_response(response, request)
    # Frozen header/binding/count rules demand that EVERY returned family row
    # belongs to the captured scope; foreign rows fail instead of disappearing.
    # Normalize order only, retaining the exact multiset. Azure is not required
    # to return identical row ordering on a later fresh-process read.
    return sorted(reader._inventory(lambda _alias: response, scope.replica_scope), key=reader._row_fingerprint)


def inventory_sha256(rows):
    return hashlib.sha256(codec.canonical(sorted(rows, key=reader._row_fingerprint))).hexdigest()


def collect_evidence(family_query, captured_query, scope):
    try:
        scope = checked_scope(scope)
        first = family_inventory(family_query, scope)
        captured = reader.collect_evidence(captured_query, scope.replica_scope)
        second = family_inventory(family_query, scope)
        require(Counter(map(reader._row_fingerprint, first)) == Counter(map(reader._row_fingerprint, second)),
            'logs_inventory_drift')
        sha = inventory_sha256(first)
        require(sha == captured['header_inventory_sha256'] == inventory_sha256(second), 'logs_inventory_drift')
        return {'captured': captured, 'family_scope': asdict(scope),
            'inventory_before': first, 'inventory_after': second, 'inventory_sha256': sha,
            'observed_execution_family_protocol_complete': True,
            'platform_replacement_excluded': False, 'zero_log_replica_absence_verified': False,
            'late_delivery_excluded': False}
    except reader.CatalogLogsError:
        raise
    except Exception:
        raise reader.CatalogLogsError('logs_record_invalid') from None


# The rehearsal's new QA is distinct from historical 707-file transport QA.
ADDITIONS = ('scripts/read_owner_task_catalog_family.py', 'tests/test_owner_task_catalog_family.py',
    'scripts/run_owner_task_isolated_provider_observation.py', 'tests/test_owner_task_isolated_provider_observation.py')
PREVIOUS_PINS = {
    'preflight.json': ('2d6e612a1d0a2ad441cc33012717e72e1a837d1c2684c6f26efc270efaea0678', '2026-10-04T14:46:50.9540129Z'),
    'acceptance.json': ('617c2942b943ab607fc77062e77eb4447edbdfadba2634e0c3276515374c1cf1', '2026-10-04T15:02:04.4349565Z')}
NEW_NAMES = ('preflight.json', 'inventory-before.json', 'inventory-after.json', 'acceptance.json')


def rehearsal():
    import verify_owner_task_catalog_canary_readback as r
    return r


def folder():
    return rehearsal().ROOT / '.work/owner-task-catalog-family-20261004'


def validate_sources(previous, current):
    r = rehearsal()
    r.require(type(previous) is dict and len(previous) == 707 and type(current) is dict
        and set(current) == set(previous) | set(ADDITIONS) and not set(previous) & set(ADDITIONS)
        and all(type(k) is str and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k,v in current.items())
        and all(current[name] == value for name,value in previous.items()), 'family_only_four_new_sources_allowed')


def previous_evidence():
    r = rehearsal()
    records = r.history()
    prepared_raw, prepared = r.load('preflight.json')
    accepted_raw, accepted = r.load('acceptance.json')
    for name, raw in (('preflight.json', prepared_raw), ('acceptance.json', accepted_raw)):
        r.receipt_binding(raw, r.record_kind(name), PREVIOUS_PINS[name])
    r.validate_sources(r.body(records['readback-correction.json'])['sources'], prepared.get('sources'))
    r.prior.validate_correction_checks(prepared.get('local_checks'))
    r.require(prepared['local_checks']['test_count'] == 2003
        and prepared.get('history_receipts') == r.history_receipts(records)
        and prepared.get('binding') == r.binding()
        and prepared.get('status') == 'prepared_readonly_owner_observed_canary_readback'
        and all(prepared.get(k) is False for k in r.FALSE_FLAGS), 'family_exact_historical_preflight_required')
    observed = prepared['observed']
    lines = r.verify_parts(accepted.get('parts'), prepared_raw, records, observed)
    expected = {'status': 'complete_owner_observed_canary_transport_retained',
        'preflight_receipt': prepared_raw['protected_receipt'], 'history_receipts': r.history_receipts(records),
        'binding': r.binding(), 'observed': observed, 'parts': accepted['parts'], 'record_bytes': 4194304,
        'chunk_count': 1024, 'frame_count': 1025, 'record_sha256': r.RECORD_SHA,
        'header_inventory_sha256': '295a3d63ed751887fa2a43d194341b6686dc657b58df0d2c570cf8e2b107b254',
        'transport_scope': 'protected_owner_observed_replica_only', 'original_automated_capture_passed': False,
        'protected_complete_record_readback': True, 'existing_jobs_unchanged': 34, 'daily_schedule_hkt': '18:00',
        'sql_calls': 0, 'model_calls': 0, **{k: False for k in r.FALSE_FLAGS}}
    r.require(r.canonical(accepted) == r.canonical(expected), 'family_exact_historical_transport_required')
    return records, prepared_raw, accepted_raw, lines


def guards(records, previous_preflight, arm):
    r = rehearsal()
    current = r.sources()
    validate_sources(r.body(previous_preflight)['sources'], current)
    historical = r.body(records['preflight.json'])
    r.require(r.prior.host.runtime_descriptor() == historical['runtime']
        and r.prior.baseline() == historical['baseline']
        and r.prior.historical_host() == historical['historical_host_receipt'], 'family_current_guard_differs')
    r.prior.validate_job(arm.get(r.prior.PATH + '?api-version=' + r.prior.API))
    env = arm.get(r.prior.TARGET.environment_resource_id + '?api-version=' + r.prior.API)
    config = env.get('properties', {}).get('appLogsConfiguration', {})
    r.require(env.get('id') == r.prior.TARGET.environment_resource_id and config.get('destination') == 'log-analytics'
        and config.get('logAnalyticsConfiguration', {}).get('customerId') == reader.WORKSPACE,
        'family_log_environment_differs')
    observed = r.observed_execution(records, arm.get(r.EXECUTION_ID + '?api-version=' + r.prior.API))
    r.require(r.sources() == current, 'family_source_changed_during_guards')
    return current, observed


def record_kind(name):
    r = rehearsal()
    r.require(type(name) is str and name in NEW_NAMES, 'family_record_name_unregistered')
    return 'catalog-family-' + name.removesuffix('.json')


def save(value, name):
    r = rehearsal()
    kind = record_kind(name)
    r.require(0 < len(r.canonical(value)) <= r.MAX_BYTES, 'family_record_oversized')
    r.require(not (folder()/name).exists(), 'family_record_consumed_inspect_only')
    r.prior.deploy.FOLDER = folder()
    raw = r.prior.deploy.save(value, name, kind)
    r.receipt_binding(raw, kind)
    r.require(r.canonical(r.prior.deploy.protected(raw, kind)) == r.canonical(value), 'family_saved_record_differs')
    return raw


def load(name):
    r = rehearsal()
    kind = record_kind(name)
    raw = r.read_local(folder(), name)
    value = r.receipt_binding(raw, kind)
    r.require(r.canonical(r.prior.deploy.protected(raw, kind)) == r.canonical(value), 'family_remote_record_differs')
    return raw, value


def references(records, previous_preflight, previous_acceptance):
    r = rehearsal()
    return {'history_receipts': r.history_receipts(records),
        'previous_preflight_receipt': previous_preflight['protected_receipt'],
        'previous_acceptance_receipt': previous_acceptance['protected_receipt']}


def prepare(arm):
    r = rehearsal()
    r.require(not (folder()/'preflight.json').exists(), 'family_preflight_consumed_inspect_only')
    before, runtime = r.sources(), r.prior.host.runtime_descriptor()
    records, old_preflight, old_acceptance, _lines = previous_evidence()
    current, observed = guards(records, old_preflight, arm)
    r.require(current == before, 'family_source_changed_before_qa')
    checks = r.prior.qa.run_local_checks()
    r.prior.validate_correction_checks(checks)
    r.require(checks['test_count'] > 2003 and r.sources() == before
        and r.prior.host.runtime_descriptor() == runtime, 'family_current_qa_or_source_differs')
    final, after = guards(records, old_preflight, arm)
    r.require(final == before and r.canonical(after) == r.canonical(observed), 'family_guard_drift_during_qa')
    return save({'status': 'prepared_readonly_observed_execution_family_rehearsal',
        'sources': current, 'runtime': runtime, 'local_checks': checks, 'allowed_additions': list(ADDITIONS),
        'binding': r.binding(), 'observed': observed, **references(records, old_preflight, old_acceptance),
        'sql_calls': 0, 'model_calls': 0, **{k: False for k in r.FALSE_FLAGS},
        'zero_log_replica_absence_verified': False, 'late_delivery_excluded': False}, 'preflight.json')


def frozen(preflight, records, old_preflight, old_acceptance, arm):
    r = rehearsal()
    r.prior.validate_correction_checks(preflight.get('local_checks'))
    current, observed = guards(records, old_preflight, arm)
    wanted = {'status': 'prepared_readonly_observed_execution_family_rehearsal',
        'sources': current, 'runtime': r.prior.host.runtime_descriptor(), 'local_checks': preflight['local_checks'],
        'allowed_additions': list(ADDITIONS), 'binding': r.binding(), 'observed': observed,
        **references(records, old_preflight, old_acceptance), 'sql_calls': 0, 'model_calls': 0,
        **{k: False for k in r.FALSE_FLAGS}, 'zero_log_replica_absence_verified': False, 'late_delivery_excluded': False}
    r.require(preflight['local_checks']['test_count'] > 2003 and r.canonical(preflight) == r.canonical(wanted),
        'family_exact_frozen_preflight_required')
    return observed


def inventory_value(rows, stage, preflight_raw, evidence):
    r = rehearsal()
    r.require(type(stage) is str and stage in ('before', 'after'), 'family_inventory_stage_unregistered')
    return {'kind': 'complete_observed_execution_family_protocol_inventory', 'stage': stage,
        'preflight_receipt': preflight_raw['protected_receipt'], 'family_scope': evidence['family_scope'],
        'inventory_sha256': evidence['inventory_sha256'], 'rows': rows,
        'zero_log_replica_absence_verified': False, 'platform_replacement_excluded': False,
        'late_delivery_excluded': False}


def collect(arm, family_query, captured_query, *, verify_only=False):
    r = rehearsal()
    r.require(type(verify_only) is bool, 'family_exact_verify_mode_required')
    r.require(verify_only or not (folder()/'acceptance.json').exists(), 'family_acceptance_exists_use_verify')
    prepared_raw, prepared = load('preflight.json')
    records, old_preflight, old_acceptance, protected_lines = previous_evidence()
    observed = frozen(prepared, records, old_preflight, old_acceptance, arm)
    scope = CatalogFamilyScope(reader.CatalogLogScope(r.prior.binding(), observed['mapping']['log_group'],
        r.prior.CONTAINER, observed['scope_start'], observed['scope_end']), r.EXECUTION)
    evidence = collect_evidence(family_query, captured_query, scope)
    r.require(evidence['captured']['lines'] == protected_lines
        and r.prior.complete_evidence(evidence['captured'], scope.replica_scope) == r.RECORD_SHA,
        'family_live_and_protected_complete_wire_differs')
    # Cap ALL complete inventories before the first protected write.
    values = {stage: inventory_value(evidence['inventory_'+stage], stage, prepared_raw, evidence)
        for stage in ('before', 'after')}
    r.require(all(0 < len(r.canonical(value)) <= r.MAX_BYTES for value in values.values()), 'family_inventory_oversized')
    receipts = {}
    for stage, value in values.items():
        name = 'inventory-' + stage + '.json'
        if verify_only or (folder()/name).exists():
            raw, retained = load(name)
            r.require(r.canonical(retained) == r.canonical(value), 'family_retained_inventory_differs')
        else:
            raw = save(value, name)
        receipts[stage] = raw['protected_receipt']
    r.require(r.canonical(frozen(prepared, records, old_preflight, old_acceptance, arm)) == r.canonical(observed),
        'family_final_guard_drift')
    value = {'status': 'observed_execution_family_transport_rehearsal_retained',
        'preflight_receipt': prepared_raw['protected_receipt'], 'inventory_receipts': receipts,
        **references(records, old_preflight, old_acceptance), 'binding': r.binding(), 'observed': observed,
        'family_scope': evidence['family_scope'], 'frame_count': len(evidence['inventory_before']),
        'record_bytes': 4194304, 'record_sha256': r.RECORD_SHA, 'inventory_sha256': evidence['inventory_sha256'],
        'observed_execution_family_protocol_complete': True, 'original_automated_capture_passed': False,
        'protected_complete_record_readback': True, 'existing_jobs_unchanged': 34, 'daily_schedule_hkt': '18:00',
        'sql_calls': 0, 'model_calls': 0, **{k: False for k in r.FALSE_FLAGS},
        'zero_log_replica_absence_verified': False, 'late_delivery_excluded': False}
    if verify_only:
        raw, accepted = load('acceptance.json')
        r.require(r.canonical(accepted) == r.canonical(value), 'family_fresh_acceptance_differs')
        return {'status': 'independent_observed_execution_family_rehearsal_verified', 'protected_receipt': raw['protected_receipt']}
    r.require(not (folder()/'acceptance.json').exists(), 'family_acceptance_exists_use_verify')
    raw = save(value, 'acceptance.json')
    return {'status': value['status'], 'protected_receipt': raw['protected_receipt']}


def main():
    import argparse
    r = rehearsal()
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'collect', 'verify'))
    args = parser.parse_args()
    r.prior.host.checked_owner()
    arm = r.ReadOnlyArm()
    if args.stage != 'verify':
        folder().mkdir(parents=True, exist_ok=True)
    if args.stage == 'prepare':
        raw = prepare(arm)
        result = {'status': raw['status'], 'tests': raw['local_checks']['test_count'], 'protected_receipt': raw['protected_receipt']}
    else:
        credential = r.prior.AzureCliCredential()
        result = collect(arm, FamilyLogsTransport(credential), reader.LogsTransport(credential),
            verify_only=args.stage == 'verify')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
