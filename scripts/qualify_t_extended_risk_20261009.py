"""Private T-only risk qualification from archived inputs; no production cache writes."""
from pathlib import Path
from collections import defaultdict
from decimal import Decimal as D, localcontext
import hashlib,json,math,random,statistics,sys
import numpy as np
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.alpha_world_t_analytics import analyze
from ibkr_etl.alpha_world_ms_crisis import market_sources,align
from ibkr_etl.benchmark_analytics import _simulated_drawdown_distribution
WORK=ROOT/'.work/alpha-t-onboarding-completion-20261009'
HISTORY=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1'

def extended(snapshot):
    metrics,daily,evidence=analyze(snapshot)
    if evidence['excluded_intervals']:raise ValueError('Unresolved gaps in daily risk sample')
    values=np.asarray(daily,dtype=float)
    mean=float(values.mean());se=float(values.std(ddof=1)/math.sqrt(len(values)))
    assert math.isclose(mean,statistics.fmean(daily),abs_tol=1e-15)
    assert math.isclose(se,statistics.stdev(daily)/math.sqrt(len(daily)),abs_tol=1e-15)
    actual=None
    if len(daily)>=252:
        wealth=np.r_[1.,np.cumprod(1+values[-252:])];actual=float(np.min(wealth/np.maximum.accumulate(wealth)-1))
    paths=_simulated_drawdown_distribution(list(daily))
    if paths:
        rng=random.Random(int.from_bytes(hashlib.sha256(','.join(f'{r:.12g}' for r in daily).encode()).digest()[:8],'big'))
        generated=[]
        for _ in range(5000):
            starts=[rng.randint(0,len(daily)-5) for _ in range(51)]
            sampled=np.asarray([daily[i+j] for i in starts for j in range(5)][:252])
            wealth=np.r_[1.,np.cumprod(1+sampled)]
            generated.append(float(np.min(wealth/np.maximum.accumulate(wealth)-1)))
        assert np.max(np.abs(np.asarray(paths)-generated))<1e-12
    threshold=float(np.quantile(paths,.05)) if paths else None
    tail=[v for v in paths if v<=threshold] if paths else []
    return {'blue_mean_daily_return':mean,'blue_standard_error':se,
      'blue_assumption':'Sample mean and conventional standard error; BLUE interpretation requires iid homoskedastic errors.',
      'expected_maximum_drawdown_1y':statistics.fmean(paths) if paths else None,
      'actual_maximum_drawdown_1y':actual,'actual_window_status':'252 sessions' if actual is not None else 'insufficient history for a full year',
      'tail_maximum_drawdown_1y':statistics.fmean(tail) if tail else None,'drawdown_simulation_paths':len(paths),
      'drawdown_horizon_sessions':252,'drawdown_block_length':5,'drawdown_tail_confidence':.95,
      'bootstrap_status':'private arithmetic verified' if paths else 'insufficient history: minimum 20 returns',
      'original_metrics':metrics,'calculation_evidence':evidence}

def main(*, approved_identity_proxy=False):
    raw=(WORK/'supplemental-inputs.json').read_bytes();d=json.loads(raw)
    proof=json.loads((WORK/'supplemental-inputs-result.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==next(r['sha256'] for r in proof['records'] if r.get('status')=='captured')
    live=json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())
    nav={(r['subject_id'],r['snapshot_date']):D(r['total_text']) for r in live['source_nav']}
    positions=defaultdict(dict)
    for r in live['position_details']:positions[r['account_id'],r['snapshot_date']][r['conid']]=r
    fx={(r['account_id'],r['rate_date'],r['from_currency'],r['to_currency']):D(r['rate_text']) for r in live['broker_fx']}
    maps=defaultdict(dict)
    for r in d['crisis_replay_components']:
        key=(r['subject_id'],r['holdings_as_of']);assert r['conid'] not in maps[key];maps[key][r['conid']]=r
    prices={}
    for r in d['crisis_prices']:
        key=(r['symbol'],r['price_date']);assert key not in prices
        assert r['currency']=='USD' and r['price_type']=='adjusted_close';prices[key]=D(str(r['price']))
    market=market_sources();days=market['calendar']
    if approved_identity_proxy:
        from ibkr_etl.alpha_world_t_crisis_policy import apply_identity_proxy
        maps={key:{conid:apply_identity_proxy(value,subject_id='alpha-world-t',crisis_start=days[0])
                   for conid,value in rows.items()} for key,rows in maps.items()}
    histfx,_=align(market['fx']['observations'],days,'rate')
    hsi,_=align(market['hedges']['hsi']['observations'],days,'price',hong_kong_sessions=True)
    spy,_=align(market['hedges']['spy']['observations'],days,'price')
    import sqlite3
    c=sqlite3.connect((HISTORY/'qualification.sqlite').resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    originals=json.loads((WORK/'broker-reconciliation.json').read_bytes())
    source_positions={(r['account_id'],r['snapshot_date'],r['conid']):r for r in originals['comparisons']['position_details']['matched']}
    revision='v3-proxy' if approved_identity_proxy else 'v2'
    output=WORK/('extended-risk-'+revision);output.mkdir(exist_ok=False);summary=[]
    for path in sorted((HISTORY/'snapshots').glob('*.json')):
        original=path.read_bytes();s=json.loads(original);day=s['data_as_of'];risk=extended(s)
        comps={r['component_id']:D(str(r['contribution'])) for r in c.execute("SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' AND snapshot_date=?",(day,))}
        problems=[];sleeves={};map_audit=[];receivables=[]
        for account in ('U16088571','U16088606'):
            held=dict(positions[account,day]);mapping=maps[account,day]
            # Only original broker-designated receivables may remain in the flat
            # NAV residual. Never silently discard an unmapped equity position.
            for conid in set(held)-set(mapping):
                source=source_positions[account,day,conid]
                raw=(WORK/'originals'/(source['source_sha256']+'.xml')).read_bytes()
                assert hashlib.sha256(raw).hexdigest()==source['source_sha256']
                attrs=[e.attrib for e in ET.fromstring(raw).iter('OpenPosition') if e.get('conid')==str(conid) and e.get('reportDate')==day.replace('-','')]
                if attrs and all(a.get('symbol','').endswith('.REC') and a.get('listingExchange')=='CORPACT' for a in attrs):
                    receivables.append({'account':account,'conid':conid,'symbol':attrs[0]['symbol'],'source_sha256':source['source_sha256'],'treatment':'Included in flat source NAV residual; not an equity replay unit'})
                    del held[conid]
            if set(held)!=set(mapping):problems.append({'account':account,'reason':'exact holdings/mapping coverage differs'});continue
            capital=nav[account,day];values=[]
            for conid,r in mapping.items():
                h=held[conid];rate=D(1) if h['currency']=='USD' else fx.get((account,day,h['currency'],'USD'))
                if rate is None or rate<=0:problems.append({'account':account,'conid':conid,'reason':'exact source FX absent'});continue
                expected=D(h['market_value_text'])*rate;recorded=D(str(r['current_market_value']))
                if abs(D(str(r['opening_weight']))-recorded/capital)>D('1e-9'):
                    problems.append({'account':account,'conid':conid,'reason':'legacy mapping weight internally inconsistent'});continue
                if r['mapping_method'] not in ('direct','market_proxy') or (r['mapping_method']=='market_proxy' and not r['proxy_reason']):
                    problems.append({'account':account,'conid':conid,'reason':'undocumented proxy'});continue
                if any((r['source_symbol'],day_) not in prices for day_ in days):problems.append({'account':account,'conid':conid,'reason':'incomplete adjusted price history'});continue
                values.append((expected,r['source_symbol']))
                map_audit.append({'account':account,'conid':conid,'source_symbol':r['source_symbol'],'mapping_method':r['mapping_method'],'proxy_reason':r['proxy_reason'],'current_value_usd':str(expected),'legacy_reference_fx_value_usd':str(recorded),'broker_fx_difference_usd':str(expected-recorded),'valuation_basis':'Exact archived broker conversion rate; legacy reference-FX weights not reused'})
                if r.get('proxy_policy_version'):
                    map_audit[-1].update({key:r[key] for key in ('proxy_policy_version','proxy_approval_date',
                        'proxy_identity_source','original_source_symbol','original_mapping_method')})
            if len(values)!=len(held):continue
            cash=capital-sum(v for v,_ in values)
            sleeves[account]=[(cash+sum(v*prices[symbol,t]/prices[symbol,days[0]] for v,symbol in values))/capital for t in days]
        crisis={'status':'unavailable','issues':problems}
        if not problems and len(sleeves)==2:
            total=sum(comps.values());series=[]
            with localcontext() as ctx:
                ctx.prec=40
                for i,t in enumerate(days):
                    f=D(str(histfx[t]))/D(str(histfx[days[0]]))
                    level=(comps['constant-cash']+sum(comps[a]*sleeves[a][i]*f for a in sleeves)+comps['hang-seng-price-index']*D(str(hsi[t]))/D(str(hsi[days[0]]))+comps['spy-settlement']*D(str(spy[t]))/D(str(spy[days[0]]))*f)/total
                    if level<=0:raise ValueError('Nonpositive crisis NAV')
                    independent=(float(comps['constant-cash'])+sum(float(comps[a])*float(sleeves[a][i])*float(f) for a in sleeves)+float(comps['hang-seng-price-index'])*hsi[t]/hsi[days[0]]+float(comps['spy-settlement'])*spy[t]/spy[days[0]]*float(f))/float(total)
                    assert math.isclose(float(level),independent,abs_tol=1e-12)
                    series.append({'date':t,'value':float(level)})
            levels=np.array([r['value'] for r in series]);maximum=float(np.min(levels/np.maximum.accumulate(levels)-1))
            assert abs(levels[0]-1)<1e-12
            crisis={'status':'private reconstructed arithmetic verified','maximum_drawdown':maximum,'holdings_as_of':day,'reporting_currency':'HKD','daily':series,'components':map_audit,'source_cash_included':True,'constant_hkd_cash':58,'additional_financing':False,'provider_prices_independently_verified':False,'proxy_components':sum(r['mapping_method']=='market_proxy' for r in map_audit)}
        crisis['receivables_in_flat_residual']=receivables
        result={'subject_id':'alpha-world-t','period_end':s['period_end'],'risk':risk,'crisis':crisis,'snapshot_sha256':hashlib.sha256(original).hexdigest(),'capture_sha256':hashlib.sha256((WORK/'supplemental-inputs.json').read_bytes()).hexdigest(),'publication_allowed':False,'production_changed':False}
        with (output/path.name).open('x') as f:json.dump(result,f,indent=2)
        assert path.read_bytes()==original
        summary.append({'period_end':s['period_end'],'bootstrap_paths':risk['drawdown_simulation_paths'],'full_year_observed_available':risk['actual_maximum_drawdown_1y'] is not None,'crisis_status':crisis['status'],'crisis_issues':problems,'maximum_drawdown':crisis.get('maximum_drawdown')})
        print(json.dumps({k:v for k,v in summary[-1].items() if k!='crisis_issues'}),flush=True)
    with (WORK/('extended-risk-summary-'+revision+'.json')).open('x') as f:json.dump({'months':summary,'production_changed':False,'original_kpis_preserved':True,'approved_identity_proxy_applied':approved_identity_proxy},f,indent=2)
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-identity-proxy',action='store_true')
    main(approved_identity_proxy=parser.parse_args().approved_identity_proxy)
