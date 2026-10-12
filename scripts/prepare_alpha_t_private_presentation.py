"""Build a private T-only presentation input from the read-only audited ledger."""
import hashlib, json, math, sqlite3, sys
from collections import defaultdict
from pathlib import Path
from ibkr_etl.alpha_world_t_analytics import analyze

ROOT=Path(__file__).resolve().parents[1]
HISTORY=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1'
WORK=ROOT/'.work/alpha-world-t-accuracy-audit-20261009'
COMPLETION=ROOT/'.work/alpha-t-onboarding-completion-20261009'
NAMES={'U16088606':('Greater China','Greater China including Taiwan','Long account NAV'),
 'U16088571':('US Tech','US Tech & Biotech','Long account NAV'),
 'hang-seng-price-index':('HSI','Hang Seng Index','Short price index'),
 'spy-settlement':('SPY','S&P 500 via SPY','Short price index'),
 'constant-cash':('Cash','Constant cash','Constant cash')}

def build_model(s):
    assert s['subject_id']=='alpha-world-t' and s['publication_allowed'] is False
    metrics,daily,evidence=analyze(s)
    assert metrics==s['risk']['metrics']
    hs=s['holdings']['holdings'];nav=float(s['portfolio_summary']['latest_nav']['total_text'])
    assert math.isclose(sum(float(r['market_value_text']) for r in hs),nav,abs_tol=1e-10)
    monthly=s['monthly_reporting']
    assert math.isclose(sum(r['contribution'] for r in monthly['security_contributions']),monthly['portfolio_monthly_return'],abs_tol=1e-10)
    portfolio={'period_start':evidence['start'],'period_end':evidence['end'],
      'return_observations':len(daily),'portfolio_total_return':metrics['total_return'],
      'portfolio_annualized_return':metrics['annualized_return'],
      'portfolio_annualized_volatility':metrics['annualized_volatility'],
      'portfolio_max_drawdown':metrics['maximum_drawdown']}
    unavailable=('benchmark_total_return','benchmark_annualized_return','benchmark_annualized_volatility',
      'benchmark_max_drawdown','excess_total_return','beta','correlation','tracking_error',
      'information_ratio','relative_win_rate','upside_capture_pct')
    portfolio.update({k:None for k in unavailable})
    risk={**metrics,'daily_expected_tail_loss':metrics['cvar_95_daily'],'return_observations':len(daily)}
    risk.update({k:None for k in ('blue_mean_daily_return','blue_standard_error','expected_maximum_drawdown_1y',
      'actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','crisis_maximum_drawdown','drawdown_simulation_paths',
      'drawdown_horizon_sessions','drawdown_block_length','drawdown_tail_confidence')})
    if 'extended_risk' in s:
        extension=s['extended_risk']
        raw=(COMPLETION/'extended-risk-v2'/f"{s['period_end']}.json").read_bytes()
        if hashlib.sha256(raw).hexdigest()!=s['qualification_evidence']['extended_risk_sha256'] or json.loads(raw)!=extension:
            raise ValueError('Extended risk differs from the independently replayed evidence')
        original=(HISTORY/'snapshots'/f"{s['period_end']}.json").read_bytes()
        if extension['snapshot_sha256']!=hashlib.sha256(original).hexdigest():
            raise ValueError('Extended risk snapshot binding differs')
        if (extension['subject_id']!='alpha-world-t' or extension['period_end']!=s['period_end']
                or extension['publication_allowed'] is not False
                or extension['risk']['original_metrics']!=metrics
                or extension['risk']['calculation_evidence']!=evidence):
            raise ValueError('Extended risk identity or original metrics differ')
        for key in ('blue_mean_daily_return','blue_standard_error','expected_maximum_drawdown_1y',
                    'actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','drawdown_simulation_paths',
                    'drawdown_horizon_sessions','drawdown_block_length','drawdown_tail_confidence'):
            risk[key]=extension['risk'][key]
        # Crisis source prices are still awaiting independent confirmation.
        # Preserve that release gate rather than publishing an arithmetic-only result.
        risk['crisis_maximum_drawdown']=None
    return {'version':'alpha-world-t-private-presentation-v1','calculation':evidence,'risk':risk,
      'primary':portfolio,'secondary':{k:None for k in unavailable},'month_rows':s['month_end_performance'],
      'constant_cash':58,'crisis_available':False,'benchmark_available':False}

def prepare(period, destination=None, *, extended_risk=False):
    s=json.loads((HISTORY/'snapshots'/f'{period}.json').read_bytes())
    metrics,_,_=analyze(s)
    independent=json.loads((WORK/'independent-reconciliation.json').read_bytes())
    check=next(r for r in independent['monthly'] if r['period_end']==period)
    for k,v in metrics.items():
        assert math.isclose(v,check['metrics'][k],abs_tol=1e-12)
    series=s['performance']['series'];months={}
    for row in series:months[row['date'][:7]]=row
    rows=[];previous=100.
    for row in months.values():
        rows.append({'date':row['date'],'portfolio':row['nav'],'portfolio_monthly_return':row['nav']/previous-1,
                     'benchmark_monthly_return':None})
        previous=row['nav']
    start=rows[-2]['date'] if len(rows)>1 else series[0]['date']
    start_nav=rows[-2]['portfolio'] if len(rows)>1 else 100.
    conn=sqlite3.connect('file:'+ (HISTORY/'qualification.sqlite').as_posix()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    def components(day):return {r['component_id']:dict(r) for r in conn.execute(
       "SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' AND snapshot_date=?",(day,))}
    before,after=components(start),components(s['data_as_of'])
    holdings=[];contributions=[];sector=defaultdict(float);sector_contrib=defaultdict(float)
    for k,(symbol,name,category) in NAMES.items():
        value=float(after[k]['contribution']);effect=(value-float(before[k]['contribution']))/start_nav
        holdings.append({'conid':k,'symbol':symbol,'description':name,'asset_category':'cash' if k=='constant-cash' else 'component',
                         'market_value_text':str(value),'currency':'HKD','quantity_text':None})
        contributions.append({'symbol':symbol,'description':name,'contribution':effect})
        sector[category]+=abs(value);sector_contrib[category]+=effect
    gross=sum(sector.values())
    s.update({'quality_status':'warning','publication_allowed':False,'month_end_performance':rows,
      'risk':{**s['risk'],'metrics':metrics,'crisis_replay':None},'holdings':{'holdings':holdings},
      'portfolio_summary':{'latest_nav':{'total_text':str(series[-1]['nav'])}},
      'sector_allocation':{'classification_basis':'composite sleeve type','classification_coverage_pct':100,
         'gross_market_value':gross,'allocation':[{'sector':k,'weight':v/gross} for k,v in sector.items()]},
      'monthly_reporting':{'available':True,'period_start':start,'period_end':s['data_as_of'],
         'method_version':'fixed-unit-component-delta-over-prior-month-nav-v1',
         'portfolio_monthly_return':rows[-1]['portfolio_monthly_return'],'security_contributions':contributions,
         'sector_contributions':[{'sector':k,'contribution':v} for k,v in sector_contrib.items()],
         'equity_contribution':sum(r['contribution'] for r in contributions),'residual_contribution':0,
         'cash_activity':{'dividends_by_symbol':[]}}})
    conn.close()
    if extended_risk:
        extension_raw=(COMPLETION/'extended-risk-v2'/f'{period}.json').read_bytes()
        hibor=json.loads((COMPLETION/'hkab-history-verification.json').read_bytes())
        cash=json.loads((COMPLETION/'fresh-cash-reconciliation-2.json').read_bytes())
        if (hibor['status']!='verified' or len(hibor['rows'])!=420
                or any(r['status']!='matched' for r in hibor['rows'])
                or cash['with_recoverable_detail_id']!=1718 or cash['ambiguous']
                or cash['duplicate_detail_ids'] or len(cash['statement_bridges'])!=16
                or not all(r['matched'] for r in cash['statement_bridges'])):
            raise ValueError('Broker or publisher qualification evidence is incomplete')
        s['extended_risk']=json.loads(extension_raw)
        s['qualification_evidence']={
            'extended_risk_sha256':hashlib.sha256(extension_raw).hexdigest(),
            'hibor_sha256':hashlib.sha256((COMPLETION/'hkab-history-verification.json').read_bytes()).hexdigest(),
            'cash_reconciliation_sha256':hashlib.sha256((COMPLETION/'fresh-cash-reconciliation-2.json').read_bytes()).hexdigest(),
            'broker_detail_ids_recovered':1718,'hibor_fixings_matched':420,
            'broker_identifiers_imported':False,'market_price_confirmation':'pending',
            'production_changed':False,'publication_allowed':False}
    build_model(s)
    destination=Path(destination) if destination else WORK/f'presentation-input-{period}.json'
    with destination.open('x',encoding='utf-8') as f:json.dump(s,f,indent=2,allow_nan=False)
    print(destination)

if __name__=='__main__':
    if sys.argv[1]=='model':print(json.dumps(build_model(json.loads(Path(sys.argv[2]).read_bytes())),allow_nan=False))
    else:prepare(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None,extended_risk='--extended-risk' in sys.argv)
