"""Private T cache qualification. This module grants no production/release authority.

Retain the complete consumed NAV/HIBOR evidence so deterministic values can be
recomputed when qualifying an isolated cache. No bootstrap, SQL or network I/O.
The production risk-snapshot guards remain in place until deployment qualification.
"""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

from .alpha_world_t_analytics import analyze, methodology_identity, MARKETS, SUBJECT_ID
from .market_calendar import CALENDARS

VERSION = 'alpha-world-t-private-core-cache-v1'
UNAVAILABLE = {
    'benchmark_comparison': 'not-qualified',
    'security_tail_attribution': 'not-qualified',
    'crisis_replay': 'independent-price-verification-pending',
    'one_year_drawdown': 'extended-risk-integration-pending',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(snapshot):
    return hashlib.sha256(canonical({'cache_version': VERSION,
        'methodology_identity': methodology_identity(), 'snapshot': snapshot}).encode()).hexdigest()


def _qualified_input(snapshot):
    cutoff = date.fromisoformat(snapshot['period_end'])
    latest_required = cutoff
    while not any(CALENDARS[m].is_session(latest_required) for m in MARKETS):
        latest_required -= timedelta(days=1)
    if not latest_required <= date.fromisoformat(snapshot['data_as_of']) <= cutoff:
        raise ValueError('T cache must cover the latest required source-market session')
    # Select only fields consumed by the core calculation. A caller's embedded
    # risk metrics, labels or report publication flags cannot become authority.
    source = deepcopy({k: snapshot[k] for k in ('subject_id', 'period_start',
        'period_end', 'data_as_of', 'reporting_currency', 'methodology_identity', 'performance')})
    source['risk'] = {'risk_free': deepcopy(snapshot['risk']['risk_free'])}
    metrics, _, evidence = analyze(source)
    return source, metrics, evidence


def build(snapshot):
    source, metrics, evidence = _qualified_input(snapshot)
    payload = {'cache_version': VERSION, 'subject_id': SUBJECT_ID,
        'as_of': source['period_end'], 'reporting_currency': 'HKD',
        'methodology_identity': methodology_identity(), 'source_snapshot': source,
        'source_sha256': fingerprint(source), 'metrics': metrics,
        'analytics_evidence': evidence, 'unavailable_capabilities': deepcopy(UNAVAILABLE),
        'publication_allowed': False, 'production_qualified': False}
    return {'payload': payload, 'payload_sha256': hashlib.sha256(canonical(payload).encode()).hexdigest()}


def validate(record, *, as_of, current_snapshot=None):
    """Check exact evidence and arithmetic; optionally check current source values."""
    payload = record['payload']
    if hashlib.sha256(canonical(payload).encode()).hexdigest() != record['payload_sha256']:
        raise ValueError('T private cache payload hash differs')
    if (payload.get('subject_id') != SUBJECT_ID or payload.get('as_of') != as_of
            or payload.get('cache_version') != VERSION
            or payload.get('reporting_currency') != 'HKD'
            or payload.get('methodology_identity') != methodology_identity()
            or payload.get('publication_allowed') is not False
            or payload.get('production_qualified') is not False
            or payload.get('unavailable_capabilities') != UNAVAILABLE):
        raise ValueError('T private cache identity or qualification scope differs')
    source, metrics, evidence = _qualified_input(payload['source_snapshot'])
    if source['period_end'] != as_of or fingerprint(source) != payload.get('source_sha256'):
        raise ValueError('T private cache source identity differs')
    # Canonical comparison deliberately distinguishes bools from numeric values.
    if canonical(metrics) != canonical(payload.get('metrics')):
        raise ValueError('T private cache numerical replay differs')
    if canonical(evidence) != canonical(payload.get('analytics_evidence')):
        raise ValueError('T private cache analytics evidence differs')
    if current_snapshot is not None:
        current, _, _ = _qualified_input(current_snapshot)
        if fingerprint(current) != payload['source_sha256']:
            raise ValueError('T private cache source values changed')
    return True
