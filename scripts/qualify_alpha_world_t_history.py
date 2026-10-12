"""Isolated T reconciliation of hash-verified Azure observations; no live writes."""
import argparse
import calendar
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from ibkr_etl.db import initialize, connect
from ibkr_etl.derived_portfolio import refresh_alpha_world_t_portfolio
from ibkr_etl.alpha_world_t_analytics import analyze, methodology_identity, MARKETS
from ibkr_etl.hibor import period_average
from ibkr_etl.market_calendar import CALENDARS

START = '2025-01-20'
ACCOUNTS = ('U16088606', 'U16088571')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,sort_keys=True,allow_nan=False)


def seed_database(path,data):
    if path.exists(): raise ValueError('Qualification database must be new')
    initialize(path)
    c=connect(path)
    c.execute("INSERT INTO ingestion_runs(run_type,started_at,status) VALUES ('isolated-verified-export',?,'success')",(data['captured_at'],))
    run=c.execute('SELECT run_id FROM ingestion_runs').fetchone()[0]
    for r in data['accounts']:
        c.execute('INSERT INTO accounts(account_id,alias,base_currency,first_seen_at,last_seen_at,source_json) VALUES (?,?,?,?,?,?)',
                  (r['account_id'],r['alias'],r['base_currency'],data['captured_at'],data['captured_at'],json.dumps(r)))
    for r in data['source_nav']:
        # Preserve actual total NAV only; cash/positions are not reconstructed.
        c.execute('INSERT INTO nav_snapshots(account_id,snapshot_date,currency,asset_class,total_text,row_sha256,last_seen_run_id) VALUES (?,?,?,?,?,?,?)',
                  (r['subject_id'],r['snapshot_date'],r['currency'],'TOTAL',r['total_text'],digest(r),run))
    for r in data['broker_fx']:
        c.execute('INSERT INTO fx_rates(account_id,rate_date,from_currency,to_currency,rate_text,row_sha256,last_seen_run_id) VALUES (?,?,?,?,?,?,?)',
                  (*[r[k] for k in ('account_id','rate_date','from_currency','to_currency','rate_text')],digest(r),run))
    keys=('instrument_id','price_date','symbol','instrument_name','price_type','currency','price','source','source_url','fetched_at')
    for r in data['market_prices']:
        c.execute('INSERT INTO market_data_prices('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',tuple(r[k] for k in keys))
    c.commit()
    return c


def independent_values(data):
    """Decimal formula implemented independently of the application evaluator."""
    nav={a:{} for a in ACCOUNTS}
    for r in data['source_nav']:
        if r['currency']!='USD': raise ValueError('Unexpected source currency')
        target=nav[r['subject_id']]
        if r['snapshot_date'] in target: raise ValueError('Duplicate source NAV')
        target[r['snapshot_date']]=Decimal(r['total_text'])
    if set(nav[ACCOUNTS[0]])!=set(nav[ACCOUNTS[1]]):
        raise ValueError('Source NAV dates differ; investigate before silently intersecting')
    days=sorted(nav[ACCOUNTS[0]])
    if days[0]!=START: raise ValueError('Missing source inception')
    def fx(day):
        for account in ACCOUNTS:
            for source,target,inverse in (('USD','HKD',False),('HKD','USD',True)):
                rows=[r for r in data['broker_fx'] if r['account_id']==account and r['from_currency']==source and r['to_currency']==target and r['rate_date']<=day]
                if rows:
                    r=max(rows,key=lambda item:item['rate_date'])
                    rate=Decimal(r['rate_text'])
                    if rate<=0 or not rate.is_finite() or (date.fromisoformat(day)-date.fromisoformat(r['rate_date'])).days>4:
                        raise ValueError('Invalid/stale independent FX')
                    return Decimal(1)/rate if inverse else rate
        raise ValueError('Missing independent FX')
    def market(instrument,day):
        rows=[r for r in data['market_prices'] if r['instrument_id']==instrument and r['price_date']<=day]
        r=max(rows,key=lambda item:item['price_date'])
        if (date.fromisoformat(day)-date.fromisoformat(r['price_date'])).days>7:
            raise ValueError('Stale independent benchmark')
        return Decimal(str(r['price']))
    def raw(day):
        rate=fx(day)
        return [nav[ACCOUNTS[0]][day]*rate,nav[ACCOUNTS[1]][day]*rate,
                market('hang-seng-price-index',day),market('spy-settlement',day)*rate]
    base=raw(START); coefficients=[Decimal(x) for x in ('91','45','-49','-45')]
    values={}
    for day in days:
        value=Decimal(58)+sum(w*current/initial for w,current,initial in zip(coefficients,raw(day),base))
        if value<=0 or not value.is_finite(): raise ValueError('Invalid independent T NAV')
        values[day]=value
    return values


def independent_metrics(snapshot):
    series=snapshot['performance']['series']
    nav=np.array([r['nav'] for r in series]); dates=[date.fromisoformat(r['date']) for r in series]
    returns=[]
    for i in range(1,len(dates)):
        gap=[dates[i-1]+timedelta(days=j) for j in range(1,(dates[i]-dates[i-1]).days)]
        if not any(CALENDARS[m].is_session(d) for d in gap for m in MARKETS):
            returns.append(nav[i]/nav[i-1]-1)
    returns=np.array(returns); sd=float(np.std(returns,ddof=1))
    rf=float(np.mean([r['annual_rate'] for r in snapshot['risk']['risk_free']['observations']]))
    threshold=np.quantile(returns,.05)
    return {'total_return':float(nav[-1]/nav[0]-1),
        'annualized_return':float((nav[-1]/nav[0])**(365.25/(dates[-1]-dates[0]).days)-1),
        'annualized_volatility':sd*math.sqrt(252),
        'sharpe_ratio':float((np.mean(returns)-((1+rf)**(1/252)-1))/sd*math.sqrt(252)) if sd else None,
        'maximum_drawdown':float(np.min(nav/np.maximum.accumulate(nav)-1)),
        'var_95_daily':max(0.,float(-threshold)),
        'cvar_95_daily':max(0.,float(-np.mean(returns[returns<=threshold]))),
        'win_rate':float(np.mean(returns>0)), 'risk_free_annual_rate':rf}


def qualify(source,expected_sha,folder):
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha: raise ValueError('Source receipt hash differs')
    data=json.loads(raw)
    if data.get('database_read_only') is not True: raise ValueError('Read-only export required')
    folder.mkdir(parents=True,exist_ok=False)
    with closing(seed_database(folder/'qualification.sqlite',data)) as c:
        immutable_tables=('accounts','nav_snapshots','fx_rates','market_data_prices')
        source_digest=lambda: digest({t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in immutable_tables})
        before=source_digest()
        refresh=refresh_alpha_world_t_portfolio(c,generated_at=data['captured_at'])
        rows=[dict(r) for r in c.execute("SELECT snapshot_date AS date,currency,total_nav AS nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' AND currency='HKD' ORDER BY snapshot_date")]
        independent=independent_values(data)
        if [r['date'] for r in rows]!=list(independent): raise ValueError('NAV date set differs')
        differences=[abs(r['nav']-float(independent[r['date']])) for r in rows]
        if max(differences)>1e-10: raise ValueError('Independent Decimal NAV differs')
        refresh_alpha_world_t_portfolio(c,generated_at=data['captured_at'])
        replay=[dict(r) for r in c.execute("SELECT snapshot_date AS date,currency,total_nav AS nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' AND currency='HKD' ORDER BY snapshot_date")]
        if replay!=rows or source_digest()!=before: raise ValueError('Replay/source immutability differs')
    rates=[{'rate_date':r['rate_date'],'annual_rate':float(r['annual_rate'])} for r in data['hkd_risk_free']
           if r['series_id']=='HKAB-HIBOR-1M' and r['tenor']=='1-month' and r['quality_status']=='pass']
    snapshots=folder/'snapshots'; snapshots.mkdir()
    months=[]; first=date.fromisoformat(START); last=date.fromisoformat(rows[-1]['date'])
    for year in range(first.year,last.year+1):
        for month in range(1,13):
            period=date(year,month,calendar.monthrange(year,month)[1])
            if period<first or period>last: continue
            series=[r for r in rows if r['date']<=period.isoformat()]
            end=series[-1]['date']
            snapshot={'subject_id':'alpha-world-t','period_start':START,'period_end':period.isoformat(),
                'data_as_of':end,'reporting_currency':'HKD','methodology_identity':methodology_identity(),
                'performance':{'series':series},'risk':{'risk_free':period_average(rates,first,date.fromisoformat(end))},
                'publication_allowed':False,'synthetic_data':False,
                'qualification_source':{'sha256':expected_sha,'captured_at':data['captured_at']}}
            metrics,_,evidence=analyze(snapshot); expected=independent_metrics(snapshot)
            errors={k:abs(v-expected[k]) for k,v in metrics.items() if v is not None}
            if max(errors.values())>1e-10: raise ValueError('Independent metrics differ')
            save(snapshots/(period.isoformat()+'.json'),snapshot)
            months.append({'period_end':period.isoformat(),'data_as_of':end,'metrics':metrics,
                'maximum_metric_error':max(errors.values()),'evidence':evidence,'snapshot_sha256':digest(snapshot)})
    save(folder/'reconciled-nav.json',rows)
    report={'status':'private-real-data-reconciled','source_sha256':expected_sha,'captured_at':data['captured_at'],
        'nav':refresh,'maximum_decimal_nav_error':max(differences),'unchanged_replay':True,'source_tables_unchanged':True,
        'source_snapshots_per_account':{a:sum(r['subject_id']==a for r in data['source_nav']) for a in ACCOUNTS},
        'months':months,'monthly_count':len(months),'methodology_identity':methodology_identity(),
        'publication_allowed':False,'template_wording_changed':False,
        'pending':['Azure T consumer/deployment qualification','Private onboarding case and reporting configuration',
                   'Presentation qualification','Template narrative mismatch retained at owner instruction; publication unresolved']}
    save(folder/'reconciliation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='months'},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--source-sha256',required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); qualify(args.source,args.source_sha256,args.output)
