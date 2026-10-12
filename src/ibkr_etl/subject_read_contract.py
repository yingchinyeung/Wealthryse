"""Read-only currency and synthetic-exposure contracts shared by consumers."""
from __future__ import annotations

import math
from typing import Mapping, Any

from .subject_onboarding import validate_reference_runtime_contract


# Correlate both keys: a privileged diagnostic connection must not accidentally
# borrow another tenant's currency. No arbitrary currency is a safe fallback.
ACTIVE_NAV_CURRENCY_SQL = """(SELECT COALESCE(s.reporting_currency, s.base_currency)
    FROM platform_subjects s WHERE s.tenant_id = subject_nav_series.tenant_id
    AND s.subject_id = subject_nav_series.subject_id)"""


def require_ms_hkd_contract(connection, *, tenant_id=None):
    clause, params = ('s.tenant_id = %s AND ', (tenant_id,)) if tenant_id else ('', ())
    rows = connection.execute('''SELECT s.subject_type, s.base_currency, s.reporting_currency,
        s.history_start_date, p.base_currency AS derived_currency,
        d.configuration_json, d.configuration_sha256
        FROM platform_subjects s
        LEFT JOIN derived_portfolios p ON p.tenant_id=s.tenant_id AND p.portfolio_id=s.subject_id
        LEFT JOIN subject_calculation_definitions d ON d.tenant_id=s.tenant_id AND d.subject_id=s.subject_id
            AND d.approval_status='approved' AND d.effective_to IS NULL
        WHERE ''' + clause + "s.subject_id = 'alpha-world-ms'", params).fetchall()
    if len(rows) != 1:
        raise ValueError('Alpha World MS requires exactly one approved current HKD contract')
    row = dict(rows[0])
    if (row['subject_type'] != 'composite_portfolio'
            or any(row[field] != 'HKD' for field in ('base_currency','reporting_currency','derived_currency'))
            or str(row['history_start_date']) != '2025-01-20'):
        raise ValueError('Alpha World MS HKD metadata is not ready; results are withheld')
    if row['configuration_json'] is None or row['configuration_sha256'] is None:
        raise ValueError('Alpha World MS has no approved current calculation definition')
    return validate_reference_runtime_contract('alpha-world-ms',row['configuration_json'],row['configuration_sha256'])


def synthetic_exposure(row: Mapping[str, Any]) -> dict[str, Any]:
    """A coefficient is not a share quantity; a source NAV is not a mark price."""
    result = dict(row)
    fields = ('coefficient','source_value','fx_rate_to_base','base_value','normalized_value')
    values = {key:float(result[key]) for key in fields}
    contribution = float(result['market_value_text'])
    if not all(math.isfinite(v) for v in (*values.values(),contribution)) or values['fx_rate_to_base'] <= 0:
        raise ValueError('Synthetic exposure contains invalid values')
    if not math.isclose(values['source_value']*values['fx_rate_to_base'],values['base_value'],rel_tol=1e-10,abs_tol=1e-9):
        raise ValueError('Synthetic source conversion differs from recorded base value')
    if not math.isclose(values['coefficient']*values['normalized_value'],contribution,rel_tol=1e-10,abs_tol=1e-9):
        raise ValueError('Synthetic coefficient/normalized contribution does not reconcile')
    if result['direction'] not in ('long','short','cash') or (
        result['direction']=='long' and values['coefficient']<=0
        or result['direction']=='short' and values['coefficient']>=0
        or result['direction']=='cash' and contribution<0):
        raise ValueError('Synthetic exposure direction differs from its coefficient')
    result.update(values)
    result.update(quantity_text=None,mark_price_text=None,
        holding_kind='synthetic_exposure',valuation_basis='coefficient-times-normalized-source',
        quantity_basis='not-applicable-no-security-units')
    return result
