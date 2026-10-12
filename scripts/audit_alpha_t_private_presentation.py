"""Read-only exported T presentation numeric evidence checks."""
import calendar,hashlib,json,math,sys,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from ibkr_etl.alpha_world_t_presentation import build_model
from audit_alpha_ms_presentation import verify_chart
A='http://schemas.openxmlformats.org/drawingml/2006/main'
C='http://schemas.openxmlformats.org/drawingml/2006/chart'
def audit(deck,source):
    s=json.loads(source.read_bytes());m=build_model(s)
    with zipfile.ZipFile(deck) as z:
        slides=[E.fromstring(z.read(f'ppt/slides/slide{i}.xml')) for i in range(1,21)]
        assert len([n for n in z.namelist() if __import__('re').fullmatch(r'ppt/slides/slide\d+\.xml',n)])==20
        texts=[' '.join(t.text or '' for t in r.iter('{'+A+'}t')) for r in slides]
        # The preserved blank allocation rows expand in native PowerPoint.
        # Keep the explanatory note below that native table, above the footer.
        pns='{http://schemas.openxmlformats.org/presentationml/2006/main}'
        notes=[sp for sp in slides[4].findall('.//'+pns+'sp')
               if ''.join(t.text or '' for t in sp.iter('{'+A+'}t')).startswith('Gross sleeve exposure:')]
        assert len(notes)==1,'Allocation explanatory note is missing or ambiguous'
        frame=notes[0].find(pns+'spPr/{'+A+'}xfrm')
        top=int(frame.find('{'+A+'}off').get('y'))/9525
        height=int(frame.find('{'+A+'}ext').get('cy'))/9525
        assert 486 <= top and top+height <= 511,'Allocation note overlaps the native table or footer'
        joined=' '.join(texts)
        for term in ('Alpha World MS','FOCUS US Tech','UST 3-month','APPLE INC','NaN','undefined','Every two weeks','no watermark'):
            assert term not in joined,term
        for term in ('Four missing snapshots','Cash records have missing dates','Source reconciliation remains incomplete',
                     'UST 3-month','USD','Original records not reconciled'):
            assert term not in joined,term
        for term in ('Alpha World T','230%','Internal/shareholder use only','period average'):
            assert term in joined,term
        assert ('1,718 broker IDs' in joined or
                ('58 HKD per initial NAV 100; no additional interest' in joined and
                 'Access requires the website approval workflow' in joined))
        def table(i):
            return [[''.join(t.text or '' for t in c.iter('{'+A+'}t')) for c in r.findall('{'+A+'}tc')]
                    for r in slides[i-1].find('.//{'+A+'}tbl').findall('{'+A+'}tr')]
        rows={r[0]:r[1] for r in table(12)}
        for name,key in {'Annualized return':'annualized_return','Annualized volatility':'annualized_volatility',
          'Risk-free rate':'risk_free_annual_rate','Daily VaR (95%)':'var_95_daily',
          'Daily Expected Tail Loss':'cvar_95_daily','Win rate':'win_rate'}.items():
            assert rows[name]==f"{m['risk'][key]*100:.2f}%",name
        assert rows['Sharpe ratio']==f"{m['risk']['sharpe_ratio']:.2f}"
        assert rows['Mean daily return']==f"{m['risk']['blue_mean_daily_return']*100:.4f}%"
        assert f"SE {m['risk']['blue_standard_error']*100:.4f}% (iid assumption)" in texts[11]
        def shape_text(i, identity):
            for shape in slides[i-1].findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}sp'):
                prop=shape.find('.//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr')
                if prop is not None and prop.get('id')==identity:
                    return ''.join(t.text or '' for t in shape.iter('{'+A+'}t'))
            raise AssertionError(identity)
        # Numeric drawdown cards are verified separately from their captions.
        dd_values=[f"{m['risk'][k]*100:.2f}%" if m['risk'][k] is not None else 'N/A'
                   for k in ('expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y')]
        for identity,value in zip(('27','32','37'),dd_values):
            assert shape_text(14,identity)==value,(identity,value)
        assert shape_text(14,'42')==(f"{m['risk']['crisis_maximum_drawdown']*100:.2f}%" if m['crisis_available'] else 'N/A')
        if m['risk']['drawdown_simulation_paths']:
            assert '5,000 paths, 252 sessions' in texts[13] and 'Mean of worst 5% of paths' in texts[13]
        else:
            assert 'N/A: fewer than 20 returns' in texts[13]
        if m['risk']['actual_maximum_drawdown_1y'] is None:
            assert 'N/A: fewer than 252 returns' in texts[13]
        assert ('15 Sep 2008 to 9 Mar 2009 replay' if m['crisis_available'] else 'Qualified replay unavailable') in texts[13]
        assert 'provisional prices and documented proxies' in joined
        controls=dict(table(14)[1:])
        assert controls['Return observations']==str(m['risk']['return_observations'])
        if m['risk']['drawdown_simulation_paths']:
            assert controls=={'Return observations':str(m['risk']['return_observations']),
                'Block length':'5 sessions','Simulation paths':'5,000','Tail confidence':'95%','Horizon':'252 sessions'}
        else:
            assert controls=={'Return observations':str(m['risk']['return_observations']),
                'Block length':'N/A','Simulation paths':'0','Tail confidence':'N/A','Horizon':'N/A'}
        monthly=table(15)
        for r in m['month_rows']:
            year,month=int(r['date'][:4]),int(r['date'][5:7])
            assert monthly[year-2024][month]==f"{r['portfolio_monthly_return']*100:.1f}%"
        charts=[]
        for n in z.namelist():
            if __import__('re').fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml',n):
                root=E.fromstring(z.read(n));charts.append((' '.join(t.text or '' for t in root.iter('{'+A+'}t')),root))
        assert len(charts)==6
        def chart(prefix):
            found=[r for name,r in charts if name.startswith(prefix)];assert len(found)==1,(prefix,[x[0] for x in charts]);return found[0]
        hs=sorted(s['holdings']['holdings'],key=lambda r:abs(float(r['market_value_text'])),reverse=True)
        gross=s['sector_allocation']['gross_market_value'];name=lambda r:r.get('description') or r['symbol']
        verify_chart(chart('Components'),[name(r) for r in hs],[round(abs(float(r['market_value_text']))/gross,6) for r in hs],'0%')
        months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sept','Oct','Nov','Dec']
        verify_chart(chart('Rebased performance comparison'),[months[int(r['date'][5:7])-1]+'-'+r['date'][2:4] for r in m['month_rows']],
            [round(r['portfolio'],4) for r in m['month_rows']],'0',require_visible_marker=len(m['month_rows'])==1)
        allocation=s['sector_allocation']['allocation']
        verify_chart(chart('Component-type weights'),[r['sector'] for r in allocation],[round(r['weight'],6) for r in allocation],'0%')
        contributions=s['monthly_reporting']['security_contributions']
        rows=sorted((r for r in contributions if r['contribution']<0),key=lambda r:r['contribution'])+sorted((r for r in contributions if r['contribution']>0),key=lambda r:r['contribution'])
        verify_chart(chart('Top five contributors'),[name(r) for r in rows],[abs(round(r['contribution'],6)) for r in rows],'0.00%',[f"{r['contribution']*100:.2f}%" for r in rows])
        rows=s['monthly_reporting']['sector_contributions']
        verify_chart(chart('Component-type contribution'),[r['sector'] for r in rows],[abs(round(r['contribution'],6)) for r in rows],'0.00%',[f"{r['contribution']*100:.2f}%" for r in rows])
        verify_chart(chart('Constant cash per initial NAV 100'),['Constant cash'],[58.],'"HKD "0')
        assert 'N/A' in texts[12]
        # Verify every printed component value and gross weight at display precision.
        for row,h in zip(table(5)[1:],hs):
            value=float(h['market_value_text']);expected=('-' if value<0 else '')+f'HKD {abs(value):,.2f}'
            assert row[4]==expected and row[5]==f'{abs(value)/gross*100:.2f}%'
    return {'status':'passed','slides':20,'financial_charts_verified':6,'risk_metrics_verified':13,
        'monthly_returns_verified':len(m['month_rows']),'component_values_verified':5,
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'deck_sha256':hashlib.sha256(deck.read_bytes()).hexdigest(),
        'scope':'Private stored-input arithmetic, bootstrap and qualified internal hypothetical crisis; benchmark comparisons unavailable',
        'publication_allowed':False}
if __name__=='__main__':print(json.dumps(audit(Path(sys.argv[1]),Path(sys.argv[2])),indent=2))
