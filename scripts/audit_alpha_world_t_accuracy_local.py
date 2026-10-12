"""Read-only independent reconciliation of T's database export and existing PDFs."""
from collections import Counter,defaultdict
from datetime import date,timedelta
from decimal import Decimal as D
import hashlib,json,math,random,sqlite3,statistics
from pathlib import Path
import numpy as np
from pypdf import PdfReader
from ibkr_etl.alpha_world_t_analytics import analyze,simulate,MARKETS,VERSION
from ibkr_etl.alpha_world_t_pdf_validation import validate
from ibkr_etl.market_calendar import CALENDARS
from qualify_alpha_world_t_history import independent_values,independent_metrics

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'.work/alpha-world-t-accuracy-audit-20261009'
HISTORY=ROOT/'.work/alpha-world-t-onboarding-20261008/history-v1'
PDFS=ROOT/'output/pdf/AlphaWorld_T_Factsheets_Layout_v7'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(name,obj):
    with (AUDIT/name).open('x',encoding='utf-8') as f:json.dump(obj,f,indent=2,default=str,allow_nan=False)
def main():
    source=AUDIT/'live-inputs.json';data=json.loads(source.read_bytes())
    proof=json.loads((AUDIT/'database-audit-result.json').read_bytes())
    receipt=next(r for r in proof['records'] if r.get('status')=='inspected')
    assert sha(source)==receipt['sha256'] and data['transaction_read_only']=='on'
    snapshots=sorted((HISTORY/'snapshots').glob('*.json'));pdfs=sorted(PDFS.glob('*.pdf'))
    protected={str(p):sha(p) for p in [source,HISTORY/'qualification.sqlite',*snapshots,*pdfs]}
    previous=json.loads((ROOT/'.work/alpha-world-t-onboarding-20261008/live-inputs.json').read_bytes())
    fresh=independent_values(data);old=independent_values(previous)
    fresh_changes=[d for d in old if old[d]!=fresh.get(d)]
    assert not fresh_changes,'Current source observations change the previously qualified NAV'
    connection=sqlite3.connect('file:'+(HISTORY/'qualification.sqlite').as_posix()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    nav=[dict(r) for r in connection.execute("SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date")]
    components=[dict(r) for r in connection.execute("SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,component_id")]
    assert len(nav)==len(fresh)==448 and all(r['currency']=='HKD' for r in nav)
    nav_errors=[abs(D(str(r['total_nav']))-fresh[r['snapshot_date']]) for r in nav]
    assert max(nav_errors)<D('1e-10')
    # Independently check every component's raw observation, conversion,
    # inception normalization, coefficient and signed contribution.
    source_nav={(r['subject_id'],r['snapshot_date']):D(r['total_text']) for r in data['source_nav']}
    rates={(r['account_id'],r['rate_date'],r['from_currency'],r['to_currency']):D(r['rate_text']) for r in data['broker_fx']}
    markets={(r['instrument_id'],r['price_date']):D(str(r['price'])) for r in data['market_prices']}
    weights={'U16088606':D('.91'),'U16088571':D('.45'),'hang-seng-price-index':D('-.49'),'spy-settlement':D('-.45')}
    def rate(day,currency):
        if currency=='HKD':return D(1)
        for account in ('U16088606','U16088571'):
            for a,b,inverse in (('USD','HKD',False),('HKD','USD',True)):
                days=[key[1] for key in rates if key[0]==account and key[2:]==(a,b) and key[1]<=day]
                if days:
                    observed=max(days);v=rates[account,observed,a,b]
                    assert v>0 and (date.fromisoformat(day)-date.fromisoformat(observed)).days<=4
                    return 1/v if inverse else v
        raise AssertionError('Missing FX')
    def raw(component,day):
        if component in ('U16088606','U16088571'):return source_nav[component,day],day
        observed=max(d for c,d in markets if c==component and d<=day)
        assert (date.fromisoformat(day)-date.fromisoformat(observed)).days<=7
        return markets[component,observed],observed
    base={c:raw(c,'2025-01-20')[0]*rate('2025-01-20','HKD' if c=='hang-seng-price-index' else 'USD') for c in weights}
    max_component_error=D(0);sum_by_day=defaultdict(D)
    for row in components:
        c=row['component_id'];day=row['snapshot_date']
        if c=='constant-cash':
            expected={'coefficient':D(58),'source_value':D(58),'fx_rate_to_base':D(1),'base_value':D(58),'normalized_value':D(1),'contribution':D(58)}
            observed=day;currency='HKD'
        else:
            value,observed=raw(c,day);currency='HKD' if c=='hang-seng-price-index' else 'USD';fx=rate(day,currency)
            converted=value*fx;normalized=100*converted/base[c]
            expected={'coefficient':weights[c],'source_value':value,'fx_rate_to_base':fx,'base_value':converted,'normalized_value':normalized,'contribution':weights[c]*normalized}
        assert row['observation_date']==observed and row['source_currency']==currency and row['base_currency']=='HKD'
        for key,value in expected.items():
            error=abs(D(str(row[key]))-value);max_component_error=max(max_component_error,error)
            assert error<D('1e-8'),(day,c,key,error)
        sum_by_day[day]+=D(str(row['contribution']))
    assert len(components)==2240
    for row in nav:
        assert abs(sum_by_day[row['snapshot_date']]-D(str(row['total_nav'])))<D('1e-10')
        assert D(str(row['cash']))==58 and abs(D(str(row['stock']))+58-D(str(row['total_nav'])))<D('1e-10')
    source_errors=[];max_nav_identity=D(0);max_position_identity=D(0);positions=defaultdict(D)
    nav_details={(r['account_id'],r['snapshot_date']):r for r in data['nav_details']}
    for row in data['nav_details']:
        error=abs(D(row['total_text'])-sum(D(row[k] or '0') for k in ('cash_text','stock_text','options_text','futures_text','bonds_text','accruals_text')))
        max_nav_identity=max(max_nav_identity,error)
        if error>D('.02'):source_errors.append({'check':'nav_identity','date':row['snapshot_date'],'account':row['account_id'],'error':str(error)})
    for row in data['position_details']:
        a,day,c=row['account_id'],row['snapshot_date'],row['currency']
        error=abs(D(row['market_value_text'])-D(row['quantity_text'])*D(row['mark_price_text']))
        max_position_identity=max(max_position_identity,error)
        if error>D('.02'):source_errors.append({'check':'position_arithmetic','date':day,'account':a,'conid':row['conid'],'error':str(error)})
        fx=D(1) if c=='USD' else rates[a,day,c,'USD']
        positions[a,day]+=D(row['market_value_text'])*fx
    max_holdings_identity=D(0)
    for key,row in nav_details.items():
        expected=sum(D(row[k] or '0') for k in ('stock_text','options_text','futures_text','bonds_text'))
        error=abs(expected-positions[key]);max_holdings_identity=max(max_holdings_identity,error)
        if error>D('.02'):source_errors.append({'check':'holdings_to_nav','key':key,'error':str(error)})
    cash_missing=[];cash_compared=0;max_cash_error=D(0)
    for row in data['cash_details']:
        if row['currency']=='BASE_SUMMARY':
            error=abs(D(row['ending_cash_text'])-D(nav_details[row['account_id'],row['snapshot_date']]['cash_text']))
            max_cash_error=max(max_cash_error,error);cash_compared+=1
            if error>D('.02'):source_errors.append({'check':'cash_to_nav','date':row['snapshot_date'],'error':str(error)})
    for account in ('U16088571','U16088606'):
        missing=sorted({d for a,d in nav_details if a==account}-{r['snapshot_date'] for r in data['cash_details'] if r['account_id']==account})
        cash_missing.extend({'account':account,'date':d} for d in missing)
    economic=defaultdict(list)
    for r in data['cash_transactions']:
        economic[(r['account_id'],r['effective_date'],r['currency'],r['amount_text'],r['description'])].append(r)
    duplicates=[{'account':k[0],'date':k[1],'currency':k[2],'amount':k[3],'description':k[4],
                  'count':len(v),'source_keys':[r['source_key'] for r in v]} for k,v in economic.items() if len(v)>1]
    save('cash-transaction-review.json',{'possible_duplicate_groups':duplicates,'missing_cash_snapshots':cash_missing,
        'classification':'Requires original source reconciliation; repeated economic keys alone do not prove duplicates'})
    monthly=[]
    manifest=json.loads((PDFS/'verification.json').read_bytes())
    expected_pdf={r['period_end']:r['pdf_sha256'] for r in manifest['results']}
    for file in snapshots:
        s=json.loads(file.read_bytes());period=s['period_end']
        for row in s['performance']['series']:assert abs(D(str(row['nav']))-fresh[row['date']])<D('1e-10')
        metrics,daily,evidence=analyze(s);independent=independent_metrics(s)
        errors={k:abs(v-independent[k]) for k,v in metrics.items() if v is not None}
        assert max(errors.values())<1e-10
        # Recompute the actual 252-draw bootstrap paths without calling renderer
        # or analytics simulation code, preserving the published deterministic seed.
        rng=random.Random(int(hashlib.sha256(f'alpha-world-t|{period}|{VERSION}'.encode()).hexdigest()[:16],16))
        sample=[float(np.prod(1+np.array([daily[rng.randrange(len(daily))] for _ in range(252)])))-1 for _ in range(1000)]
        actual,mean=simulate(daily,period);simulation_error=max(abs(a-b) for a,b in zip(actual,sample))
        assert simulation_error<1e-12 and abs(float(np.mean(sample))-mean)<1e-12
        pdf=PDFS/f'AlphaWorld_T_Factsheet_{period}.pdf';assert sha(pdf)==expected_pdf[period]
        validation=validate(pdf,s,template_path=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf')
        text=PdfReader(pdf).pages[0].extract_text()
        for k,value in independent.items():
            if k=='risk_free_annual_rate':expected=f'{value:.2%}'
            elif k=='sharpe_ratio':expected='N/A' if value is None else f'{value:.2f}'
            elif k=='win_rate':expected=f'{value:.2%}'
            else:expected=f'{-abs(value) if k in ("var_95_daily","cvar_95_daily") else value:+.2%}'
            assert expected in text,(period,k,expected)
        monthly.append({'period_end':period,'data_as_of':s['data_as_of'],'metrics':independent,
            'max_metric_error':max(errors.values()),'max_bootstrap_error':simulation_error,'pdf_validation':validation,
            'nav_observations':len(s['performance']['series']),'risk_returns':len(daily),
            'excluded_intervals':evidence['excluded_intervals'],'short_history':evidence['short_history']})
        print(period+' independent metrics, simulation and PDF PASS',flush=True)
    last={r['component_id']:r for r in components if r['snapshot_date']=='2026-09-30'}
    total=sum(r['contribution'] for r in last.values())
    exposure={k:r['contribution']/total for k,r in last.items()}
    report={'status':'arithmetic_pass_with_data_and_publication_gaps','audit_source_sha256':sha(source),
      'captured_at':data['captured_at'],'live_t_nav_rows':len(data['existing_t_nav']),
      'live_t_report_snapshots':len(data['reporting_snapshots']),'live_t_report_generations':len(data['report_generation_runs']),
      'local_nav_rows':len(nav),'local_component_rows':len(components),'maximum_nav_error':str(max(nav_errors)),
      'maximum_component_error':str(max_component_error),'current_source_changes_vs_factsheet_inputs':fresh_changes,
      'source_checks':{'nav_rows':len(data['nav_details']),'positions':len(data['position_details']),
          'cash_summaries':cash_compared,'max_nav_identity_error':str(max_nav_identity),
          'max_position_arithmetic_error':str(max_position_identity),'max_holdings_to_nav_error':str(max_holdings_identity),
          'max_cash_error':str(max_cash_error),'arithmetic_discrepancies':source_errors},
      'cash_record_gaps':{'missing_cash_snapshots':cash_missing,'transaction_rows':len(data['cash_transactions']),
          'empty_transaction_type':sum(not r['transaction_type'] for r in data['cash_transactions']),
          'empty_transaction_id':sum(not r['transaction_id'] for r in data['cash_transactions']),
          'repeated_economic_key_groups':len(duplicates)},
      'september_current_exposure_weights':exposure,'september_gross_risky_exposure':sum(abs(v) for k,v in exposure.items() if k!='constant-cash'),
      'monthly':monthly,'source_files_unchanged':all(sha(Path(p))==h for p,h in protected.items()),
      'presentation_status':'No T PPTX or registered T presentation found; renderer rejects T HKD',
      'limitations':['Live T calculations not populated','Original broker archives not yet reconciled in this audit',
          'Stored benchmark and fixing values not independently re-observed from publishers in this audit',
          'Static asset-allocation percentages are inception coefficients, not current weights',
          'Retained dynamic-rotation/below-2x narrative conflicts with fixed-unit 230% initial gross-risky model']}
    assert report['source_files_unchanged'] and not source_errors
    save('independent-reconciliation.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='monthly'},indent=2))

if __name__=='__main__':main()
