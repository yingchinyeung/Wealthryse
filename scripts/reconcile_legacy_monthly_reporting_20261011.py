"""Enrich missing legacy monthly analytics only when frozen NAV reconciles."""
from collections import defaultdict
from copy import deepcopy
from datetime import date
import hashlib,json,math
from ibkr_etl.util import canonical_json

def enrich(payload,fresh):
    old=deepcopy(payload)
    if payload.get('subject_id')!='U16088571' or payload.get('reporting_currency')!='USD':raise ValueError('Only reviewed USD legacy scope is allowed')
    if (payload.get('monthly_reporting') or {}).get('available'):raise ValueError('Do not overwrite existing monthly analytics')
    if fresh.get('quality_status')!='pass':raise ValueError('Passing reconstructed analytics required')
    m=fresh['data'];end=date.fromisoformat(payload['period_end']);start=end.replace(day=1).isoformat()
    methods={'monthly-reporting-attribution-v1','monthly-reporting-attribution-v2-full-nav-coverage'}
    if m.get('available') is not True or m.get('reporting_currency')!='USD' or m.get('method_version') not in methods or m.get('fx_conversion',{}).get('missing_currencies')!=[]:raise ValueError('Incomplete monthly calculation contract')
    def number(x):
        if isinstance(x,bool):raise ValueError('Boolean is not a financial value')
        try:value=float(x)
        except (TypeError,ValueError) as e:raise ValueError('Missing financial value') from e
        if not math.isfinite(value):raise ValueError('Nonfinite financial value')
        return value
    def equal(x,y,tolerance=1e-10):return abs(number(x)-number(y))<=tolerance
    series=payload['performance']['series'];dates=[r['date'] for r in series]
    if dates!=sorted(set(dates)) or any(r.get('currency')!='USD' or number(r['nav'])<=0 for r in series):raise ValueError('Unique ordered positive frozen USD NAV required')
    if any(d>payload['period_end'] for d in dates):raise ValueError('Future frozen NAV is forbidden')
    before=[r for r in series if r['date']<start];inside=[r for r in series if start<=r['date']<=payload['period_end']]
    observations=before[-1:]+inside
    if len(observations)<2 or m.get('daily_observations')!=len(observations)-1 or m.get('period_start')!=observations[1]['date'] or m.get('period_end')!=observations[-1]['date']:raise ValueError('Monthly observations differ from frozen NAV')
    if m['method_version']=='monthly-reporting-attribution-v2-full-nav-coverage':
        coverage=m.get('position_coverage') or {};missing=coverage.get('missing_position_dates');residual=coverage.get('residual_only_intervals');matched=coverage.get('matched_position_intervals')
        if not isinstance(missing,list) or not isinstance(residual,list) or missing!=sorted(set(missing)) or residual!=sorted(set(residual)) or not isinstance(matched,int) or isinstance(matched,bool) or matched<1:raise ValueError('Explicit position coverage required')
        if not set(missing)<={r['date'] for r in observations} or not set(residual)<={r['date'] for r in observations[1:]} or coverage.get('nav_intervals')!=len(observations)-1 or matched+len(residual)!=len(observations)-1:raise ValueError('Position coverage does not reconcile')
    expected=number(observations[-1]['nav'])/number(observations[0]['nav'])-1
    if not equal(m['portfolio_monthly_return'],expected):raise ValueError('Monthly return differs from frozen NAV')
    securities=m['security_contributions'];identities=[r['conid'] for r in securities]
    if len(set(identities))!=len(identities) or any(not str(k).strip() for k in identities):raise ValueError('Duplicate or missing security identity')
    sectors=defaultdict(float)
    for row in securities:
        if not isinstance(row.get('sector'),str) or not row['sector'].strip():raise ValueError('Missing sector identity')
        sectors[row['sector']]+=number(row['contribution'])
    actual=m['sector_contributions'];names=[r['sector'] for r in actual]
    if len(set(names))!=len(names) or set(names)!=set(sectors) or any(not equal(r['contribution'],sectors[r['sector']]) for r in actual):raise ValueError('Sector contribution differs from security effects')
    equity=sum(number(r['contribution']) for r in securities)
    if not equal(m['equity_contribution'],equity) or not equal(equity+number(m['residual_contribution']),expected):raise ValueError('Contribution reconciliation failed')
    cash=m['cash_activity'];keys=('total_dividends','interest_received','interest_paid','fees_and_withholding','other_internal_cash')
    if not equal(sum(number(cash[k]) for k in keys),cash['net_listed_cash_activity'],0.005):raise ValueError('Cash reconciliation failed')
    if not equal(sum(number(r['amount']) for r in cash['dividends_by_symbol']),cash['total_dividends'],0.005):raise ValueError('Dividend detail does not reconcile')
    tables={r.get('table') for r in fresh.get('citations',[])}
    if not {'subject_nav_series','subject_position_series','subject_cash_activity_series','instrument_classifications'}<=tables:raise ValueError('Required reconstruction sources missing')
    result=deepcopy(payload);result['monthly_reporting']=deepcopy(m)
    result['source_citations']['monthly_reporting']=[*deepcopy(fresh['citations']),{'method':'legacy-monthly-enrichment-frozen-nav-reconciled-v1',
        'source_payload_sha256':hashlib.sha256(canonical_json(payload).encode()).hexdigest(),'calculation_sha256':hashlib.sha256(canonical_json(fresh).encode()).hexdigest(),
        'frozen_nav_risk_returns_unchanged':True}]
    if payload!=old or any(result[k]!=old[k] for k in old if k not in ('monthly_reporting','source_citations')):raise ValueError('Unrelated or original data changed')
    return result
