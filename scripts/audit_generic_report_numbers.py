"""Independent OOXML checks against a reporting-currency-normalized snapshot."""
import hashlib, math, re, zipfile
from xml.etree import ElementTree as E
A='http://schemas.openxmlformats.org/drawingml/2006/main'
C='http://schemas.openxmlformats.org/drawingml/2006/chart'

def require(value,message):
    if not value:raise ValueError(message)

def audit(path,p):
    require(p['subject_id'] in ('U16088571','U16088606','hong-kong-china'),'Generic account required')
    require(p['reporting_currency']=='USD' and p['holdings']['currency']=='USD' and p['sector_allocation']['currency']=='USD','Normalized USD evidence required')
    def text(root):return ''.join(t.text or '' for t in root.iter('{'+A+'}t'))
    def pc(x,d=2):return 'N/A' if x is None else f'{float(x)*100:.{d}f}%'
    def money(x):return ('-' if x<0 else '')+f'${abs(x):,.2f}'
    def security(r):return ' '.join(str(r.get('description') or '').split()) or str(r.get('symbol') or 'Security').strip()
    holdings=sorted(p['holdings']['holdings'],key=lambda r:abs(float(r['market_value_text'])),reverse=True)
    gross=sum(abs(float(r['market_value_text'])) for r in holdings)
    require(math.isclose(gross,p['sector_allocation']['gross_market_value'],abs_tol=1e-8),'Gross holdings mismatch')
    require(all(r['currency']=='USD' for r in holdings),'Native currency survived normalization')
    monthly=p['monthly_reporting'];cash=monthly['cash_activity'];risk=p['risk']['metrics'];months=p['month_end_performance']
    with zipfile.ZipFile(path) as z:
        slides={i:E.fromstring(z.read(f'ppt/slides/slide{i}.xml')) for i in range(1,21)}
        def table(i):
            tables=slides[i].findall('.//{'+A+'}tbl');require(len(tables)==1,'Expected one financial table')
            return [[text(c) for c in r.findall('{'+A+'}tc')] for r in tables[0].findall('{'+A+'}tr')]
        charts={}
        for n in z.namelist():
            if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n):
                root=E.fromstring(z.read(n));title=root.find('.//{'+C+'}title')
                charts[text(title)]=root
        require(len(charts)==6,'Exactly six unique charts required')
        def verify(prefix,categories,values,labels=None):
            found=[r for t,r in charts.items() if t.startswith(prefix)];require(len(found)==1,'Unique chart missing: '+prefix)
            series=found[0].findall('.//{'+C+'}ser');require(len(series)==len(values),'Chart series count differs')
            for s,wanted in zip(series,values):
                actual_categories=[e.text for e in s.findall('.//{'+C+'}cat//{'+C+'}v')]
                actual=[float(e.text) for e in s.findall('.//{'+C+'}val//{'+C+'}v')]
                require(actual_categories==categories,'Chart categories differ: '+prefix)
                require(len(actual)==len(wanted) and all(math.isclose(x,y,rel_tol=0,abs_tol=1e-9) for x,y in zip(actual,wanted)),'Chart values differ: '+prefix)
                if labels is not None:
                    require([text(e) for e in s.findall('.//{'+C+'}dLbl')]==labels,'Signed chart labels differ: '+prefix)
        verify('Top 10 holdings',[security(r) for r in holdings[:10]],[[round(abs(float(r['market_value_text']))/gross,6) for r in holdings[:10]]])
        month_names=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sept','Oct','Nov','Dec']
        verify('Rebased performance comparison',[month_names[int(r['date'][5:7])-1]+'-'+r['date'][2:4] for r in months],[[round(float(r[k]),4) for r in months] for k in ('portfolio','benchmark')])
        allocation=p['sector_allocation']['allocation']
        verify('Sector weights',[r['sector'] for r in allocation],[[round(float(r['weight']),6) for r in allocation]])
        negatives=sorted((r for r in monthly['security_contributions'] if r['contribution']<0),key=lambda r:r['contribution'])[:5]
        positives=sorted((r for r in monthly['security_contributions'] if r['contribution']>0),key=lambda r:r['contribution'],reverse=True)[:5]
        effects=negatives+list(reversed(positives)) or [{'symbol':'No effect','contribution':0}]
        verify('Top five contributors',[security(r) for r in effects],[[abs(round(r['contribution'],6)) for r in effects]],[pc(r['contribution']) for r in effects])
        effects=monthly['sector_contributions'] or [{'sector':'Unclassified','contribution':0}]
        verify('Sector contribution',[r['sector'] for r in effects],[[abs(round(r['contribution'],6)) for r in effects]],[pc(r['contribution']) for r in effects])
        dividends=cash['dividends_by_symbol'][:5] or [{'symbol':'None recorded','amount':0}]
        verify('Dividends received',[r['symbol'] for r in dividends],[[round(r['amount'],2) for r in dividends]])
        rows=table(5)
        for i,r in enumerate(holdings[:10],1):
            value=float(r['market_value_text']);expected='$'+f'{math.floor(value+0.5):,}'
            require(rows[i][4]==expected and rows[i][5]==pc(abs(value)/gross),'Holding value/weight differs')
        metrics={r[0]:r[1] for r in table(12)[1:]}
        for label,key in {'Annualized return':'annualized_return','Annualized volatility':'annualized_volatility','Risk-free rate':'risk_free_annual_rate','Daily VaR (95%)':'var_95_daily','Daily Expected Tail Loss':'daily_expected_tail_loss','Win rate':'win_rate'}.items():
            require(metrics[label]==pc(risk[key]),'Risk metric differs: '+label)
        require(metrics['Sharpe ratio']==f"{risk['sharpe_ratio']:.2f}" and metrics['BLUE mean daily return']==pc(risk['blue_mean_daily_return'],4),'Sharpe/mean differs')
        cash_rows={r[0]:r[1] for r in table(11)[1:]}
        for label,key in {'Dividends':'total_dividends','Interest received':'interest_received','Interest paid':'interest_paid','Fees and withholding':'fees_and_withholding','Net listed cash activity':'net_listed_cash_activity'}.items():
            require(cash_rows[label]==money(cash[key]),'Cash row differs: '+label)
        total=sum(cash[k] for k in ('total_dividends','interest_received','interest_paid','fees_and_withholding','other_internal_cash'))
        require(abs(total-cash['net_listed_cash_activity'])<0.005,'Cash subtotal reconciliation failed')
        require('other internal cash '+money(cash['other_internal_cash']) in text(slides[11]),'Other cash explanation absent')
        rows={r[0]:r[1:] for r in table(13)[1:]}
        primary=p['performance_comparison']['metrics'];secondary=p['secondary_performance_comparison']['metrics']
        for label,key in {'Total return':'total_return','Annualized return':'annualized_return','Annualized volatility':'annualized_volatility','Maximum drawdown':'max_drawdown'}.items():
            require(rows[label]==[pc(primary['portfolio_'+key]),pc(primary['benchmark_'+key]),pc(secondary['benchmark_'+key])],'Comparison numbers differ: '+label)
        require(rows['Excess total return'][1:]==[f"{float(x['excess_total_return'])*100:.2f} pp" for x in (primary,secondary)],'Excess return units differ')
    return {'status':'passed','financial_charts_verified':6,'risk_metrics_verified':8,'cash_rows_verified':5,
            'holdings_verified':min(10,len(holdings)),'comparison_rows_verified':5,'output_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
