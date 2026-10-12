"""Read-only MS/T model-cash display, never a broker cash ledger or HKD amount."""
from __future__ import annotations
from datetime import date
import math
from .subject_onboarding import validate_reference_runtime_contract
from .subject_read_contract import synthetic_exposure


def model_cash_view(*,tenant_id,subject_id,reporting_currency,as_of,nav,components,configuration,configuration_sha256):
    if subject_id not in ('alpha-world-ms','alpha-world-t'):
        raise ValueError('Model cash display is qualified only for MS and T')
    contract=validate_reference_runtime_contract(subject_id,configuration,configuration_sha256)
    currency=contract['base_currency'];day=str(as_of);date.fromisoformat(day)
    if currency!='HKD' or reporting_currency!=currency or day<contract['normalization_date']:
        raise ValueError('Model cash date or reporting currency differs from the approved contract')
    level=float(nav)
    if not math.isfinite(level) or level<=0:raise ValueError('Positive finite normalized NAV required')
    expected={s['component_id']:s for s in contract['components']};rows={}
    for raw in components:
        row=dict(raw);key=row['component_id']
        if str(row['tenant_id'])!=str(tenant_id) or row['portfolio_id']!=subject_id or str(row['snapshot_date'])!=day or row['base_currency']!=currency:
            raise ValueError('Model cash component tenant, subject, date or currency differs')
        if key in rows or key not in {*expected,'constant-cash'}:raise ValueError('Duplicate or unexpected model component')
        converted=synthetic_exposure({**row,'market_value_text':str(row['contribution'])})
        coefficient=converted['coefficient'];value=float(converted['market_value_text'])
        if key=='constant-cash':
            cash=float(contract['constant_cash'])
            if row['component_type']!='cash' or row['direction']!='cash' or row['source_currency']!=currency or any(converted[k]!=v for k,v in {'coefficient':cash,'source_value':cash,'fx_rate_to_base':1.,'base_value':cash,'normalized_value':1.}.items()) or value!=cash:
                raise ValueError('Constant cash differs from the approved normalized cash component')
        else:
            spec=expected[key]
            if coefficient!=float(spec['coefficient']) or row['component_type']!=spec['component_type'] or row['source_currency']!=spec['source_currency']:
                raise ValueError('Model sleeve differs from the approved composition')
        rows[key]=converted
    if set(rows)!={*expected,'constant-cash'}:raise ValueError('Complete same-date model components required')
    if not math.isclose(math.fsum(float(r['market_value_text']) for r in rows.values()),level,rel_tol=0,abs_tol=1e-9):
        raise ValueError('Same-date model components do not reconcile to normalized NAV')
    cash=float(rows['constant-cash']['market_value_text'])
    return {'as_of':day,'normalization_date':contract['normalization_date'],'currency_context':currency,
        'normalized_value':cash,'portfolio_normalized_nav':level,'nav_weight':cash/level,
        'unit':'normalized NAV units','is_broker_balance':False,'interest_bearing':False,
        'message':'This is the fixed non-interest-bearing cash component of the model, expressed in normalized NAV units, not an HKD bank or broker balance. Its share of current model NAV changes as NAV changes. This model has no independently executed cash transactions.'}


def load_model_cash(connection,*,tenant_id,subject_id,reporting_currency,as_of):
    """The caller must authorize subject access before invoking this reader."""
    if subject_id not in ('alpha-world-ms','alpha-world-t'):raise ValueError('Unqualified model cash subject')
    definitions=connection.execute('''SELECT configuration_json,configuration_sha256
        FROM subject_calculation_definitions WHERE tenant_id=%s AND subject_id=%s
        AND approval_status='approved' AND effective_to IS NULL''',(tenant_id,subject_id)).fetchall()
    if len(definitions)!=1:raise ValueError('Exactly one approved current model definition required')
    nav=connection.execute('''SELECT snapshot_date,total_text,currency FROM subject_nav_series
        WHERE tenant_id=%s AND subject_id=%s AND currency=%s AND snapshot_date<=%s
        ORDER BY snapshot_date DESC LIMIT 1''',(tenant_id,subject_id,reporting_currency,as_of)).fetchone()
    if nav is None:return None
    components=connection.execute('''SELECT tenant_id,portfolio_id,snapshot_date,component_id,component_name,
        component_type,direction,coefficient,source_currency,base_currency,source_value,fx_rate_to_base,
        base_value,normalized_value,contribution,observation_date FROM synthetic_portfolio_components
        WHERE tenant_id=%s AND portfolio_id=%s AND base_currency=%s AND snapshot_date=%s
        ORDER BY component_id''',(tenant_id,subject_id,reporting_currency,nav['snapshot_date'])).fetchall()
    return model_cash_view(tenant_id=tenant_id,subject_id=subject_id,reporting_currency=reporting_currency,
        as_of=nav['snapshot_date'],nav=nav['total_text'],components=components,
        configuration=definitions[0]['configuration_json'],configuration_sha256=definitions[0]['configuration_sha256'])
