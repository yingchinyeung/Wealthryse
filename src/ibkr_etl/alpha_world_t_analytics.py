"""Read-only T HKD/HIBOR qualification; not wired to production publication.

The full Greater China account is retained. No Taiwan exclusion, source-cash
replacement, Treasury fallback or additional projected interest is permitted.
"""
from __future__ import annotations

from .alpha_world_ms_analytics import _analyze, _simulate, PATHS
from .hibor import BASIS, load_period_average
from .market_calendar import CALENDARS, CALENDAR_VERSION
from .subject_onboarding import (
    approved_reference_definitions, configuration_sha256,
    validate_reference_runtime_contract,
)

SUBJECT_ID = 'alpha-world-t'
VERSION = 'alpha-world-t-hkd-hibor-v1'
MARKETS = ('XNYS', 'XHKG', 'XSHG', 'XTAI')


def methodology_identity():
    return {
        'subject_id': SUBJECT_ID, 'analytics_version': VERSION,
        'contract_sha256': configuration_sha256(approved_reference_definitions()[SUBJECT_ID]),
        'reporting_currency': 'HKD', 'inception': '2025-01-20',
        'risk_free_averaging_basis': BASIS, 'calendar_version': CALENDAR_VERSION,
        'session_calendars': list(MARKETS),
        'interest_basis': 'actual-source-NAV-no-additional-charge',
    }


def analyze(snapshot):
    if snapshot.get('subject_id') != SUBJECT_ID:
        raise ValueError('T analytics cannot be used for another subject')
    if snapshot.get('reporting_currency') != 'HKD':
        raise ValueError('T requires HKD calculations, not currency relabeling')
    if snapshot.get('period_start') != '2025-01-20':
        raise ValueError('T inception observation is missing; do not silently rebase')
    if snapshot.get('methodology_identity') != methodology_identity():
        raise ValueError('T HKD/HIBOR methodology identity differs')
    # Validate coverage even on adjacent dates: a missing gap must not be needed
    # to discover that a source market calendar year is unqualified.
    from datetime import date
    for row in snapshot['performance']['series']:
        day = date.fromisoformat(row['date'])
        for market in MARKETS:
            CALENDARS[market].is_session(day)
    try:
        metrics, daily, evidence = _analyze(
            snapshot, subject_id=SUBJECT_ID, version=VERSION, markets=MARKETS)
    except ValueError as exc:
        raise ValueError(str(exc).replace('MS ', 'T ')) from exc
    from .hibor import period_average
    rates = snapshot['risk']['risk_free']
    verified = period_average(rates['observations'], date.fromisoformat(evidence['start']),
                              date.fromisoformat(evidence['end']))
    if rates.get('rate_date') != verified['rate_date'] or rates.get('quality_status') != 'pass':
        raise ValueError('T HIBOR fixing date/quality evidence differs')
    evidence['session_calendars'] = list(MARKETS)
    return metrics, daily, evidence


def simulate(daily_returns, period_end, *, paths=PATHS):
    import math
    if any(not math.isfinite(r) or r <= -1 for r in daily_returns):
        raise ValueError('T simulation requires finite returns greater than -100%')
    try:
        return _simulate(daily_returns, period_end, paths=paths,
                         subject_id=SUBJECT_ID, version=VERSION)
    except ValueError as exc:
        raise ValueError(str(exc).replace('MS ', 'T ')) from exc


def require_contract(connection, *, tenant_id=None):
    """Require one exact tenant-scoped approved T calculation contract."""
    if not tenant_id:
        raise ValueError('T stored-input qualification requires an explicit tenant')
    clause, params = ('s.tenant_id = ? AND ', (tenant_id,)) if tenant_id else ('', ())
    records = connection.execute('''SELECT s.subject_type, s.base_currency, s.reporting_currency,
        s.history_start_date, p.base_currency AS derived_currency,
        d.configuration_json, d.configuration_sha256
        FROM platform_subjects s
        LEFT JOIN derived_portfolios p ON p.tenant_id=s.tenant_id AND p.portfolio_id=s.subject_id
        LEFT JOIN subject_calculation_definitions d ON d.tenant_id=s.tenant_id AND d.subject_id=s.subject_id
            AND d.approval_status='approved' AND d.effective_to IS NULL
        WHERE ''' + clause + 's.subject_id = ?', params + (SUBJECT_ID,)).fetchall()
    if len(records) != 1:
        raise ValueError('T requires exactly one approved current HKD contract')
    row = dict(records[0])
    if (row['subject_type'] != 'composite_portfolio'
            or any(row[k] != 'HKD' for k in ('base_currency', 'reporting_currency', 'derived_currency'))
            or str(row['history_start_date']) != '2025-01-20'):
        raise ValueError('T HKD metadata is not ready; results are withheld')
    if row['configuration_json'] is None or row['configuration_sha256'] is None:
        raise ValueError('T has no approved current calculation definition')
    return validate_reference_runtime_contract(SUBJECT_ID, row['configuration_json'], row['configuration_sha256'])


def load_risk_snapshot(connection, as_of, *, tenant_id=None):
    """Require the approved T contract and complete stored HIBOR fixings."""
    require_contract(connection,tenant_id=tenant_id)
    clause, params = ('tenant_id = ? AND ', (tenant_id,)) if tenant_id else ('', ())
    records = connection.execute('SELECT snapshot_date,currency,total_text FROM subject_nav_series WHERE '
        + clause + 'subject_id = ? AND currency = ? AND snapshot_date <= ? ORDER BY snapshot_date',
        params + (SUBJECT_ID, 'HKD', as_of)).fetchall()
    rows = [{'date': str(r['snapshot_date']), 'currency': str(r['currency']),
             'nav': float(r['total_text'])} for r in records]
    if not rows or rows[0]['date'] != '2025-01-20':
        raise ValueError('T approved HKD inception history is not ready')
    rates = load_period_average(connection, rows[0]['date'], rows[-1]['date'], tenant_id=tenant_id)
    snapshot = {'subject_id': SUBJECT_ID, 'period_start': rows[0]['date'],
        'period_end': as_of, 'data_as_of': rows[-1]['date'], 'reporting_currency': 'HKD',
        'methodology_identity': methodology_identity(),
        'performance': {'series': rows}, 'risk': {'risk_free': rates}}
    metrics, _, evidence = analyze(snapshot)
    return snapshot, metrics, evidence
