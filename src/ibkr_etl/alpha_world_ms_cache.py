"""Read-only identity checks for disposable MS analytics, not report authority."""
from datetime import date
import math

from .alpha_world_ms_analytics import VERSION as ANALYTICS_VERSION
from .hibor import period_average
from .market_calendar import CALENDAR_VERSION, benchmark_session_on_or_before
from .subject_onboarding import approved_reference_definitions, configuration_sha256


UNAVAILABLE = {
    'benchmark_comparison': 'not-qualified-hibor-spread-methodology',
    'security_tail_attribution': 'not-qualified',
    'crisis_replay': 'not-qualified',
    'one_year_drawdown': 'not-qualified',
}


def methodology_identity():
    from .alpha_world_ms_hkd import VERSION as FORMULA_VERSION
    return {
        'formula_version': FORMULA_VERSION,
        'analytics_version': ANALYTICS_VERSION,
        'contract_sha256': configuration_sha256(approved_reference_definitions()['alpha-world-ms']),
        'reporting_currency': 'HKD',
        'inception': '2025-01-20',
        'calendar_version': CALENDAR_VERSION,
        'interest_basis': 'actual-source-nav-only-no-additional-charge',
    }


def validate_payload(payload):
    """No source queries or simulations on reads; verify contained HIBOR evidence.

    Source corrections are checked by the worker's full-value fingerprint. This
    guard does not claim a stored payload is fresh against the live database.
    """
    if payload.get('reporting_currency') != 'HKD' or payload.get('methodology_identity') != methodology_identity():
        raise ValueError('MS cache currency or methodology differs from the deployed contract')
    if payload.get('unavailable_capabilities') != UNAVAILABLE or payload.get('benchmark_id') is not None:
        raise ValueError('Unqualified MS capabilities must remain explicitly unavailable')
    if payload.get('expected_tail_loss_contributors') != {'top': [], 'bottom': []} or payload.get('crisis_replay') is not None:
        raise ValueError('MS cache includes unqualified attribution or crisis results')
    evidence = payload['analytics_evidence']
    start, end = date.fromisoformat(evidence['start']), date.fromisoformat(evidence['end'])
    if start.isoformat() != '2025-01-20' or not start < end <= date.fromisoformat(payload['as_of']):
        raise ValueError('MS cache period is invalid')
    cutoff = date.fromisoformat(payload['as_of'])
    if end < max(benchmark_session_on_or_before(b, cutoff) for b in ('GSPC', 'HSI')):
        raise ValueError('MS cache does not cover the required joint-market session')
    required = {
        'version': ANALYTICS_VERSION, 'reporting_currency': 'HKD',
        'calendar_version': CALENDAR_VERSION,
        'growth_annualization': '365.25/calendar days',
        'risk_annualization': '252 validated session returns/year',
    }
    if any(evidence.get(k) != v for k, v in required.items()):
        raise ValueError('MS cache analytics evidence differs')
    if (not isinstance(evidence.get('nav_observations'), int) or evidence['nav_observations'] < 3
            or not isinstance(evidence.get('daily_return_observations'), int)
            or not 2 <= evidence['daily_return_observations'] < evidence['nav_observations']):
        raise ValueError('MS cache observation counts are invalid')
    digest = evidence.get('source_value_sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('MS cache source fingerprint is invalid')
    rate = payload['risk_free']
    verified = period_average(rate['observations'], start, end)
    for key in ('series_id', 'currency', 'tenor', 'annual_rate', 'period_start', 'period_end',
                'rate_date', 'averaging_basis', 'source_value_sha256', 'observation_count',
                'fixing_calendar_version', 'quality_status', 'observations'):
        if rate.get(key) != verified[key]:
            raise ValueError('MS cache HIBOR evidence differs: ' + key)
    if (evidence.get('risk_free_averaging_basis') != verified['averaging_basis']
            or evidence.get('risk_free_source_value_sha256') != verified['source_value_sha256']):
        raise ValueError('MS cache risk-free lineage differs')
    metrics = payload['metrics']
    required_metrics = ('total_return', 'annualized_return', 'annualized_volatility',
        'maximum_drawdown', 'var_95_daily', 'cvar_95_daily', 'win_rate', 'risk_free_annual_rate')
    for key in required_metrics:
        if isinstance(metrics.get(key), bool) or not math.isfinite(float(metrics[key])):
            raise ValueError('MS cache metric is invalid: ' + key)
    if 'sharpe_ratio' not in metrics or (metrics['sharpe_ratio'] is not None
            and not math.isfinite(float(metrics['sharpe_ratio']))):
        raise ValueError('MS cache Sharpe ratio is invalid')
    if metrics['risk_free_annual_rate'] != verified['annual_rate']:
        raise ValueError('MS cache metrics used a different risk-free rate')
    if any(metrics.get(k) is not None for k in ('crisis_maximum_drawdown',
            'expected_maximum_drawdown_1y', 'actual_maximum_drawdown_1y', 'tail_maximum_drawdown_1y')):
        raise ValueError('MS cache includes unqualified drawdown metrics')
