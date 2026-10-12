"""Read-only MS rendering model. Original frozen snapshots are never changed."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import sys
from .alpha_world_ms_analytics import analyze
from .alpha_world_t_presentation import extended_metrics

VERSION='alpha-world-ms-presentation-hkd-hibor-v2'

def build_model(snapshot):
    if snapshot.get('subject_id')!='alpha-world-ms' or snapshot.get('reporting_currency')!='HKD':
        raise ValueError('MS presentation requires the qualified HKD subject')
    if snapshot.get('publication_allowed') or snapshot.get('quality_status')=='fail':
        raise ValueError('MS presentation requires a private non-failed frozen input')
    metrics,daily,evidence=analyze(snapshot)
    if snapshot['risk']['metrics']!=metrics:raise ValueError('Frozen MS metrics differ from recomputation')
    crisis=snapshot['risk'].get('crisis_replay')
    if crisis:
        from .alpha_world_ms_crisis import validate
        validate(crisis,as_of=snapshot['data_as_of'],holdings=snapshot['holdings']['holdings'],
                 nav=snapshot['portfolio_summary']['latest_nav']['total_text'])
    holdings=snapshot['holdings']['holdings'];values=[float(r['market_value_text']) for r in holdings]
    if any(r.get('currency')!='HKD' for r in holdings) or not all(math.isfinite(v) for v in values):
        raise ValueError('MS component values require finite HKD observations')
    nav=float(snapshot['portfolio_summary']['latest_nav']['total_text'])
    if not math.isclose(sum(values),nav,abs_tol=1e-9):raise ValueError('Signed component values do not reconcile to NAV')
    sector=snapshot['sector_allocation']
    if not math.isclose(sum(abs(v) for v in values),float(sector['gross_market_value']),abs_tol=1e-9):
        raise ValueError('Gross component exposure differs')
    if not math.isclose(sum(float(r['weight']) for r in sector['allocation']),1.,abs_tol=1e-10):
        raise ValueError('Component-type weights do not sum to one')
    for name in ('performance_comparison','secondary_performance_comparison'):
        if (snapshot.get(name) or {}).get('metrics'):
            raise ValueError('MS benchmark results need separate qualification')
    monthly=snapshot.get('monthly_reporting') or {}
    if not monthly.get('available'):raise ValueError('Validated monthly reporting required')
    monthly_return=float(monthly['portfolio_monthly_return'])
    components=monthly['security_contributions']
    if not math.isclose(sum(float(r['contribution']) for r in components)+float(monthly['residual_contribution']),monthly_return,abs_tol=1e-10):
        raise ValueError('Monthly contributions do not reconcile')
    previous=float(snapshot['performance']['series'][0]['nav']);month_rows=[]
    for row in snapshot['month_end_performance']:
        nav=float(row['portfolio'])
        if not math.isfinite(nav) or nav<=0:raise ValueError('Invalid monthly NAV')
        month_rows.append({**row,'portfolio_monthly_return':nav/previous-1,'benchmark_monthly_return':None})
        previous=nav
    if not month_rows or not math.isclose(month_rows[-1]['portfolio_monthly_return'],monthly_return,abs_tol=1e-10):
        raise ValueError('Monthly NAV return differs from reporting ledger')
    portfolio={
        'period_start':evidence['start'],'period_end':evidence['end'],
        'return_observations':len(daily),'portfolio_total_return':metrics['total_return'],
        'portfolio_annualized_return':metrics['annualized_return'],
        'portfolio_annualized_volatility':metrics['annualized_volatility'],
        'portfolio_max_drawdown':metrics['maximum_drawdown'],
    }
    unavailable=('benchmark_total_return','benchmark_annualized_return','benchmark_annualized_volatility',
        'benchmark_max_drawdown','excess_total_return','beta','correlation','tracking_error',
        'information_ratio','relative_win_rate','upside_capture_pct')
    portfolio.update({name:None for name in unavailable})
    risk={**metrics,'daily_expected_tail_loss':metrics['cvar_95_daily'],'return_observations':len(daily)}
    risk.update({name:None for name in ('blue_mean_daily_return','blue_standard_error',
        'expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y',
        'crisis_maximum_drawdown','drawdown_simulation_paths','drawdown_horizon_sessions',
        'drawdown_block_length','drawdown_tail_confidence')})
    # One-year drawdown estimates use T's owner-approved bootstrap of observed
    # daily returns, only when no return interval spans a missing session.
    if not evidence['excluded_intervals']:
        extended=extended_metrics(daily)
        risk.update({name:extended[name] for name in ('expected_maximum_drawdown_1y',
            'actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','drawdown_simulation_paths',
            'drawdown_horizon_sessions','drawdown_block_length','drawdown_tail_confidence')})
    if crisis:risk['crisis_maximum_drawdown']=crisis['maximum_drawdown']
    cash=[r for r in snapshot['holdings']['holdings'] if r.get('asset_category')=='cash']
    if len(cash)!=1 or cash[0].get('currency')!='HKD':raise ValueError('Qualified constant cash sleeve required')
    cash_value=float(cash[0]['market_value_text'])
    if not math.isfinite(cash_value):raise ValueError('Invalid constant cash value')
    return {'version':VERSION,'calculation':evidence,'primary':portfolio,
        'secondary':{name:None for name in unavailable},'risk':risk,'month_rows':month_rows,
        'constant_cash':cash_value,'benchmark_available':False,'crisis_available':bool(crisis),
        'risk_free_description':'1M HIBOR, inception-to-valuation period average',
        'source_value_sha256':evidence['source_value_sha256']}

def main():
    raw=Path(sys.argv[1]).read_bytes();model=build_model(json.loads(raw))
    model['snapshot_sha256']=hashlib.sha256(raw).hexdigest()
    print(json.dumps(model,sort_keys=True,allow_nan=False))

if __name__=='__main__':main()
