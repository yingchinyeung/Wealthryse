"""T-only analytics acceptance; never approve definitions, reports or access.

The caller owns the transaction. Provisional market data remains restricted to
internal/shareholder use and is not represented as independently certified.
"""
from datetime import date
import os

from .alpha_world_t_analytics import require_contract
from .alpha_world_t_runtime import validate_payload
from .provisional_benchmark import _refresh_and_materialize
from .risk_snapshots import compute_risk_snapshot, store_risk_snapshot
from .util import canonical_json
from .website_benchmarks import T_HKD_COMPARISON_VERSION


def validate_benchmark_result(result):
    rows = result.get('benchmarks', [])
    expected = {'HSI': ('primary', 'HKD'), 'GSPC': ('secondary', 'USD')}
    if (result.get('account_id') != 'alpha-world-t'
            or result.get('reporting_currency') != 'HKD'
            or result.get('status') != 'success'
            or result.get('publication_allowed') is not False
            or len(rows) != 2 or {r.get('benchmark_id') for r in rows} != set(expected)):
        raise ValueError('T requires both approved HKD website comparisons')
    for row in rows:
        role, currency = expected[row['benchmark_id']]
        if (row.get('mapping_type') != role or row.get('native_currency') != currency
                or row.get('reporting_currency') != 'HKD' or row.get('available') is not True
                or row.get('quality_status') not in ('pass', 'warn')
                or int(row.get('comparison_rows') or 0) < 30):
            raise ValueError('T website comparison coverage or currency failed')
    return result


def refresh_onboarding_analytics(connection, *, tenant_id):
    """Validate and store T evidence in one caller-controlled transaction."""
    if not tenant_id or os.environ.get('PORTFOLIO_TENANT_ID') != tenant_id:
        raise ValueError('T onboarding tenant differs from configured connection')
    require_contract(connection, tenant_id=tenant_id)
    cases = connection.execute('''SELECT c.case_id FROM account_onboarding_cases c
        WHERE c.tenant_id=? AND c.target_subject_id='alpha-world-t'
        AND c.status NOT IN ('cancelled','active') AND EXISTS (
            SELECT 1 FROM account_onboarding_approvals a WHERE a.tenant_id=c.tenant_id
            AND a.subject_id=c.target_subject_id AND a.approval_type='external_market_data'
            AND a.approval_status='approved' AND a.revoked_at IS NULL)''', (tenant_id,)).fetchall()
    if len(cases) != 1:
        raise ValueError('T requires one open case and an approved external-data scope')
    latest = connection.execute('''SELECT MAX(snapshot_date) AS latest FROM subject_nav_series
        WHERE tenant_id=? AND subject_id='alpha-world-t' AND currency='HKD' ''',
        (tenant_id,)).fetchone()['latest']
    if latest is None:
        raise ValueError('T validated HKD NAV is missing')
    as_of = str(latest)
    payload, timings, source_sha = compute_risk_snapshot(connection, tenant_id=tenant_id,
        subject_id='alpha-world-t', as_of=as_of)
    validate_payload(payload)
    crisis = payload['crisis_replay']
    if (crisis['qualification_status'] != 'qualified_internal'
            or crisis['holdings_as_of'] != as_of or crisis['observations'] < 100):
        raise ValueError('T current source-bound crisis acceptance failed')
    benchmark = validate_benchmark_result(_refresh_and_materialize(connection,
        account_id='alpha-world-t', start_date=date(2025, 1, 20),
        end_date=date.fromisoformat(as_of), ticker_factory=None, commit=False))
    stored = connection.execute('''SELECT benchmark_id,reporting_currency,calculation_version,
            COUNT(*) AS observations,MAX(observation_date) AS latest
        FROM performance_comparison_daily WHERE tenant_id=? AND subject_id='alpha-world-t'
        AND reporting_currency='HKD' AND calculation_version=?
        GROUP BY benchmark_id,reporting_currency,calculation_version''',
        (tenant_id, T_HKD_COMPARISON_VERSION)).fetchall()
    if (len(stored) != 2 or {r['benchmark_id'] for r in stored} != {'HSI', 'GSPC'}
            or any(int(r['observations']) < 30 or str(r['latest']) != as_of for r in stored)):
        raise ValueError('T stored comparisons are incomplete or stale')
    store_risk_snapshot(connection, tenant_id=tenant_id, subject_id='alpha-world-t',
        as_of=as_of, payload=payload, timings=timings, source_data_sha256=source_sha)
    evidence = {'subject_id':'alpha-world-t', 'as_of':as_of, 'reporting_currency':'HKD',
        'benchmark':benchmark, 'risk_source_sha256':source_sha,
        'crisis_replay':{key:crisis[key] for key in ('calculation_version','input_sha256',
            'holdings_as_of','observations','qualification_status','limitations')},
        'classification':{'status':'not_applicable','classification_basis':'approved composite sleeves'},
        'internal_shareholder_only':True, 'provider_prices_independently_verified':False,
        'onboarding_activated':False, 'report_approvals_changed':False}
    updated = connection.execute('''UPDATE account_onboarding_checks SET status='pass',
        message=?,evidence_json=?::jsonb,checked_at=now()
        WHERE case_id=? AND check_id='risk_analytics_validated' ''',
        ('T HKD benchmarks and source-bound risk analytics passed internal-use validation; provisional-data restrictions remain.',
         canonical_json(evidence), str(cases[0]['case_id'])))
    if updated.rowcount != 1:
        raise ValueError('T risk acceptance check was not uniquely updated')
    return evidence
