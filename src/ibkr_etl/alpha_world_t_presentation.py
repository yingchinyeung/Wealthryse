"""Pure T rendering model; no private filesystem fixtures or publication authority."""
from __future__ import annotations
import hashlib,json,math,statistics,sys
from pathlib import Path
from .alpha_world_t_analytics import analyze
from .alpha_world_t_crisis import validate as validate_crisis, COMPONENTS
from .benchmark_analytics import _simulated_drawdown_distribution,_quantile,_drawdown

VERSION='alpha-world-t-presentation-hkd-hibor-v1'

def require(value,message):
    if not value:raise ValueError(message)

def extended_metrics(daily):
    require(len(daily)>=2 and all(math.isfinite(r) and r>-1 for r in daily),'Valid T daily return sample required')
    paths=_simulated_drawdown_distribution(daily)
    cutoff=_quantile(paths,.05)
    tail=[r for r in paths if cutoff is not None and r<=cutoff]
    return {'blue_mean_daily_return':statistics.fmean(daily),
        'blue_standard_error':statistics.stdev(daily)/math.sqrt(len(daily)),
        'expected_maximum_drawdown_1y':statistics.fmean(paths) if paths else None,
        'actual_maximum_drawdown_1y':_drawdown(daily[-252:]) if len(daily)>=252 else None,
        'tail_maximum_drawdown_1y':statistics.fmean(tail) if tail else None,
        'drawdown_simulation_paths':len(paths),'drawdown_horizon_sessions':252,
        'drawdown_block_length':5,'drawdown_tail_confidence':.95}

def build_model(snapshot):
    require(snapshot.get('subject_id')=='alpha-world-t' and snapshot.get('reporting_currency')=='HKD',
            'T presentation requires its qualified HKD inputs')
    require(snapshot.get('publication_allowed') is False and snapshot.get('quality_status')!='fail',
            'T requires non-failed internal-only inputs')
    metrics,daily,evidence=analyze(snapshot)
    require(snapshot['risk']['metrics']==metrics,'Frozen T metrics differ from recomputation')
    require(not evidence['excluded_intervals'],'T risk sample has unresolved return intervals')
    holdings=snapshot['holdings']['holdings']
    require(len(holdings)==5 and {r['conid'] for r in holdings}==COMPONENTS,'T component identities differ')
    values={r['conid']:float(r['market_value_text']) for r in holdings}
    require(all(r['currency']=='HKD' for r in holdings) and all(math.isfinite(v) for v in values.values()),
            'T component values must be finite HKD observations')
    require(values['constant-cash']==58 and values['U16088606']>0 and values['U16088571']>0
            and values['hang-seng-price-index']<0 and values['spy-settlement']<0,'T signed composition differs')
    nav=float(snapshot['portfolio_summary']['latest_nav']['total_text'])
    require(math.isfinite(nav) and nav>0 and math.isclose(sum(values.values()),nav,abs_tol=1e-9,rel_tol=0),
            'T signed components do not reconcile to NAV')
    series=snapshot['performance']['series']
    require(math.isclose(float(series[-1]['nav']),nav,abs_tol=1e-9,rel_tol=0),'T summary NAV differs from history')
    sector=snapshot['sector_allocation']
    require(math.isclose(sum(abs(v) for v in values.values()),float(sector['gross_market_value']),abs_tol=1e-9,rel_tol=0),
            'T gross component exposure differs')
    expected_weights={'Long account NAV':abs(values['U16088606'])+abs(values['U16088571']),
        'Short price index':abs(values['hang-seng-price-index'])+abs(values['spy-settlement']),
        'Constant cash':58.}
    allocation=sector['allocation']
    require(len(allocation)==3 and {r['sector'] for r in allocation}==set(expected_weights),
            'T component-type allocation differs')
    for row in allocation:
        require(math.isclose(float(row['weight']),expected_weights[row['sector']]/sector['gross_market_value'],
                            abs_tol=1e-12,rel_tol=0),'T component-type weight differs')
    for name in ('performance_comparison','secondary_performance_comparison'):
        require(not (snapshot.get(name) or {}).get('metrics'),'T benchmark results require separate qualification')
    months={}
    for row in series:months[row['date'][:7]]=row
    previous=float(series[0]['nav']);month_rows=[]
    for row in months.values():
        value=float(row['nav'])
        month_rows.append({'date':row['date'],'portfolio':value,'portfolio_monthly_return':value/previous-1,
                           'benchmark_monthly_return':None})
        previous=value
    supplied=snapshot['month_end_performance']
    require(len(supplied)==len(month_rows),'T monthly history coverage differs')
    for actual,expected in zip(supplied,month_rows):
        require(actual['date']==expected['date'] and float(actual['portfolio'])==expected['portfolio'],
                'T monthly NAV differs from full history')
        if 'portfolio_monthly_return' in actual:
            require(math.isclose(actual['portfolio_monthly_return'],expected['portfolio_monthly_return'],abs_tol=1e-12,rel_tol=0),
                    'T monthly return differs from full history')
    monthly=snapshot['monthly_reporting']
    require(monthly.get('available') is True and monthly['period_end']==snapshot['data_as_of'],
            'Dated T monthly reporting inputs required')
    monthly_return=float(monthly['portfolio_monthly_return'])
    require(math.isclose(monthly_return,month_rows[-1]['portfolio_monthly_return'],abs_tol=1e-12,rel_tol=0),
            'T monthly contribution period differs from NAV history')
    require(math.isclose(sum(float(r['contribution']) for r in monthly['security_contributions']),monthly_return,abs_tol=1e-10,rel_tol=0)
        and math.isclose(sum(float(r['contribution']) for r in monthly['sector_contributions']),monthly_return,abs_tol=1e-10,rel_tol=0)
        and monthly['residual_contribution']==0,'T component contributions do not reconcile')
    crisis=snapshot['risk'].get('crisis_replay')
    if crisis:
        validate_crisis(crisis,as_of=snapshot['data_as_of'],holdings=holdings,nav=snapshot['portfolio_summary']['latest_nav']['total_text'])
    risk={**metrics,**extended_metrics(daily),'daily_expected_tail_loss':metrics['cvar_95_daily'],
          'return_observations':len(daily),'crisis_maximum_drawdown':crisis['maximum_drawdown'] if crisis else None}
    portfolio={'period_start':evidence['start'],'period_end':evidence['end'],'return_observations':len(daily),
        'portfolio_total_return':metrics['total_return'],'portfolio_annualized_return':metrics['annualized_return'],
        'portfolio_annualized_volatility':metrics['annualized_volatility'],'portfolio_max_drawdown':metrics['maximum_drawdown']}
    unavailable=('benchmark_total_return','benchmark_annualized_return','benchmark_annualized_volatility',
        'benchmark_max_drawdown','excess_total_return','beta','correlation','tracking_error',
        'information_ratio','relative_win_rate','upside_capture_pct')
    portfolio.update({k:None for k in unavailable})
    return {'version':VERSION,'calculation':evidence,'risk':risk,'primary':portfolio,
        'secondary':{k:None for k in unavailable},'month_rows':month_rows,'constant_cash':58,
        'benchmark_available':False,'crisis_available':bool(crisis),
        'risk_free_description':'1M HIBOR, inception-to-valuation period average',
        'source_value_sha256':evidence['source_value_sha256']}

def main():
    raw=Path(sys.argv[1]).read_bytes();model=build_model(json.loads(raw))
    model['snapshot_sha256']=hashlib.sha256(raw).hexdigest()
    print(json.dumps(model,sort_keys=True,allow_nan=False))

if __name__=='__main__':main()
