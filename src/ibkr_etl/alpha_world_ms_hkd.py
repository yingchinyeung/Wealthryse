"""Side-effect-free HKD methodology preview from recorded sleeve source values.

Neither raw Flex observations nor frozen/published report snapshots are mutated.
The portfolio is re-normalized AFTER each sleeve is translated to HKD; this is
not a label change or conversion of an already normalized USD total.
"""
from datetime import date
import copy
import hashlib
import json
import math

from .derived_portfolio import _CrossFxRates
from .hibor import period_average
from .alpha_world_ms_analytics import analyze
from .subject_onboarding import configuration_sha256, validate_reference_runtime_contract

VERSION = 'alpha-world-ms-hkd-fixed-unit-v1'


def rebuild(inspection, cutoff='2026-09-30'):
    contracts = [r for r in inspection['contracts'] if r['subject_id']=='alpha-world-ms']
    if len(contracts)!=1:
        raise ValueError('Exactly one MS calculation definition required')
    if contracts[0].get('approval_status')!='approved':
        raise ValueError('Source calculation definition is not approved')
    old = contracts[0]['configuration_json']
    old = json.loads(old) if isinstance(old,str) else old
    if configuration_sha256(old)!=contracts[0]['configuration_sha256']:
        raise ValueError('Source contract hash differs')
    contract = copy.deepcopy(old)
    contract['base_currency'] = 'HKD'
    validate_reference_runtime_contract('alpha-world-ms',contract,configuration_sha256(contract))
    start = contract['normalization_date']
    expected = {r['component_id']:r for r in contract['components']}
    preferred = [r['component_id'] for r in contract['components'] if r['component_type']=='account']
    fx = _CrossFxRates(inspection['broker_fx'])
    grouped = {}
    for row in inspection['components']:
        if start<=row['snapshot_date']<=cutoff:
            components=grouped.setdefault(row['snapshot_date'],{})
            if row['component_id'] in components:
                raise ValueError('Duplicate source component observation')
            components[row['component_id']] = row
    if start not in grouped:
        raise ValueError('Missing inception source components')
    parents = {r['snapshot_date']:float(r['total_nav']) for r in inspection['nav'] if r['currency']=='USD'}
    normalized, audit = {}, []
    for component_id, spec in expected.items():
        row = grouped[start][component_id]
        rate, fx_date = fx.rate(spec['source_currency'],'HKD',start,preferred_accounts=preferred)
        normalized[component_id] = float(row['source_value'])*rate
        if not math.isfinite(normalized[component_id]) or normalized[component_id]<=0:
            raise ValueError('Invalid inception source value')
    series, holdings_by_date = [], {}
    for day, components in sorted(grouped.items()):
        if set(components)!=set(expected)|{'constant-cash'}:
            raise ValueError('Incomplete component identity on '+day)
        previous_total = sum(float(r['contribution']) for r in components.values())
        if not math.isclose(previous_total,parents[day],rel_tol=1e-12,abs_tol=1e-10):
            raise ValueError('Recorded source contributions disagree with parent NAV')
        holdings = []
        for component_id, spec in expected.items():
            row = components[component_id]
            if row['source_currency']!=spec['source_currency'] or float(row['coefficient'])!=float(spec['coefficient']):
                raise ValueError('Component contract drift')
            if row['observation_date']>day:
                raise ValueError('Future sleeve source observation')
            if (date.fromisoformat(day)-date.fromisoformat(row['observation_date'])).days>int(contract['market_carry_days']):
                raise ValueError('Sleeve source observation exceeds approved carry')
            rate, fx_date = fx.rate(spec['source_currency'],'HKD',day,preferred_accounts=preferred)
            if (date.fromisoformat(day)-date.fromisoformat(fx_date)).days>4:
                raise ValueError('Broker FX is stale on '+day)
            converted = float(row['source_value'])*rate
            if not math.isfinite(converted) or converted<=0:
                raise ValueError('Invalid sleeve source value')
            value = 100*converted/normalized[component_id]*float(spec['coefficient'])
            holdings.append({'conid':component_id,'symbol':spec['component_name'],
                'description':spec['component_name'],'currency':'HKD','market_value_text':str(value),
                'asset_category':spec['component_type'],'direction':spec['direction']})
            audit.append({'date':day,'component_id':component_id,'source_currency':spec['source_currency'],
                'source_value':float(row['source_value']),'source_observation_date':row['observation_date'],
                'fx_date':fx_date,'fx_to_hkd':rate,'normalization_hkd':normalized[component_id],
                'coefficient':float(spec['coefficient']),'contribution_hkd':value})
        cash=float(contract['constant_cash'])
        holdings.append({'conid':'constant-cash','symbol':'Non-interest-bearing cash','currency':'HKD',
            'market_value_text':str(cash),'asset_category':'cash','direction':'cash'})
        value=sum(float(r['market_value_text']) for r in holdings)
        if not math.isfinite(value) or value<=0:
            raise ValueError('Invalid rebuilt HKD NAV')
        series.append({'date':day,'currency':'HKD','nav':value})
        holdings_by_date[day]=holdings
    if not math.isclose(series[0]['nav'],100,abs_tol=1e-10):
        raise ValueError('Inception no longer equals 100')
    return {'version':VERSION,'contract':contract,'contract_sha256':configuration_sha256(contract),
        'previous_contract_sha256':configuration_sha256(old),'series':series,'holdings':holdings_by_date,
        'source_audit':audit,'source_audit_sha256':hashlib.sha256(json.dumps(audit,sort_keys=True,allow_nan=False).encode()).hexdigest()}


def factsheet_snapshot(history, fixings, period_end):
    rows = [r for r in history['series'] if r['date']<=period_end]
    if not rows:
        raise ValueError('No HKD history before period')
    start, end = rows[0]['date'],rows[-1]['date']
    rates = period_average(fixings,date.fromisoformat(start),date.fromisoformat(end))
    holdings = copy.deepcopy(history['holdings'][end])
    month_ends = {}
    for row in rows:
        month_ends[row['date'][:7]] = {'date':row['date'],'portfolio':row['nav']}
    snapshot = {'subject_id':'alpha-world-ms','period_start':start,'period_end':period_end,'data_as_of':end,
        'reporting_currency':'HKD','formula_version':VERSION,'snapshot_version':'private-methodology-validation-v1',
        'performance':{'series':copy.deepcopy(rows)},'portfolio_summary':{'positions':5,
            'account':{'account_id':'alpha-world-ms','base_currency':'HKD','alias':'Alpha World MS'},
            'latest_nav':{'snapshot_date':end,'currency':'HKD','total_text':str(rows[-1]['nav'])}},
        'holdings':{'holdings':holdings},'sector_allocation':{'classification_basis':'composite sleeve type',
            'gross_market_value':sum(abs(float(r['market_value_text'])) for r in holdings),'classifications':[]},
        'month_end_performance':list(month_ends.values()),'risk':{'risk_free':rates},
        'publication_allowed':False,'publication_blockers':['Private methodology validation; not released'],
        'methodology_evidence':{k:history[k] for k in ('version','contract_sha256','previous_contract_sha256','source_audit_sha256')},
        'quality_status':'pass'}
    metrics,_,evidence = analyze(snapshot)
    snapshot['risk']['metrics']=metrics
    snapshot['risk']['analytics_evidence']=evidence
    return snapshot


def load_risk_snapshot(connection, as_of, *, tenant_id=None):
    """Use the full HKD NAV sample for website/report/cache metrics alike."""
    from .subject_read_contract import require_ms_hkd_contract
    require_ms_hkd_contract(connection,tenant_id=tenant_id)
    clause, params = ('tenant_id = ? AND ', (tenant_id,)) if tenant_id else ('', ())
    records = connection.execute('SELECT snapshot_date,currency,total_text FROM subject_nav_series WHERE '
        + clause + "subject_id = 'alpha-world-ms' AND currency = 'HKD' AND snapshot_date <= ? ORDER BY snapshot_date",
        params + (as_of,)).fetchall()
    rows=[{'date':str(r['snapshot_date']),'currency':str(r['currency']),'nav':float(r['total_text'])} for r in records]
    if not rows or rows[0]['date']!='2025-01-20':
        raise ValueError('Owner-approved Alpha World MS HKD inception history is not ready')
    from .hibor import load_period_average
    rates=load_period_average(connection,rows[0]['date'],rows[-1]['date'],tenant_id=tenant_id)
    from .alpha_world_ms_cache import methodology_identity
    snapshot={'subject_id':'alpha-world-ms','period_start':rows[0]['date'],'period_end':as_of,
        'methodology_identity':methodology_identity(),
        'data_as_of':rows[-1]['date'],'reporting_currency':'HKD','performance':{'series':rows},'risk':{'risk_free':rates}}
    metrics,_,evidence=analyze(snapshot)
    return snapshot,metrics,evidence
