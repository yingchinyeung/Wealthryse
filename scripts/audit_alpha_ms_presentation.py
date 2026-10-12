"""Independent exported-package checks for the MS-only presentation path."""
import argparse
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET
from ibkr_etl.alpha_world_ms_presentation import build_model
from ibkr_etl.presentation_design import platform_content_expectations
from validate_monthly_presentation import validate
A='http://schemas.openxmlformats.org/drawingml/2006/main'
C='http://schemas.openxmlformats.org/drawingml/2006/chart'

def verify_ms_contribution_heading(text):
    if 'Component Drivers of Monthly Return' not in text or 'Sector Drivers of Relative Performance' in text:
        raise ValueError('MS contribution heading must describe absolute monthly return')

def verify_chart(root, categories, values, number_format, signed_labels=None, *, require_visible_marker=False):
    """Compare exported native evidence, including the semantic value axis."""
    series=root.findall('.//{'+C+'}ser')
    if len(series)!=1:raise ValueError('Unexpected chart series count')
    actual_categories=[e.text for e in series[0].findall('.//{'+C+'}cat//{'+C+'}v')]
    actual_values=[float(e.text) for e in series[0].findall('.//{'+C+'}val//{'+C+'}v')]
    if actual_categories!=categories:raise ValueError('Chart categories differ from frozen observations')
    if actual_values!=values:raise ValueError('Chart values differ from frozen observations')
    if require_visible_marker:
        marker=series[0].find('{'+C+'}marker')
        symbol=None if marker is None else marker.find('{'+C+'}symbol')
        size=None if marker is None else marker.find('{'+C+'}size')
        if symbol is None or symbol.get('val') in (None,'none') or size is None or float(size.get('val','0'))<=0:
            raise ValueError('A single observed month requires a visible chart marker')
    axes=root.findall('.//{'+C+'}valAx')
    if len(axes)!=1 or axes[0].find('{'+C+'}numFmt') is None or axes[0].find('{'+C+'}numFmt').get('formatCode')!=number_format:
        raise ValueError('Chart value-axis format differs from its unit')
    if signed_labels is not None:
        labels=series[0].findall('.//{'+C+'}dLbl')
        actual=[''.join(e.text or '' for e in label.iter('{'+A+'}t')) for label in labels]
        if actual!=signed_labels:raise ValueError('Contribution labels lose their source signs')
        axis_title=' '.join(e.text or '' for e in axes[0].iter('{'+A+'}t'))
        if 'Magnitude (signed labels)' not in axis_title:raise ValueError('Contribution magnitude axis is ambiguous')

def fonts(path):
    with zipfile.ZipFile(path) as z:
        return sorted({e.attrib['typeface'] for n in z.namelist() if n.endswith('.xml')
            for e in ET.fromstring(z.read(n)).iter() if e.attrib.get('typeface') and not e.attrib['typeface'].startswith('+')})

def audit(path,snapshot_path):
    payload=json.loads(snapshot_path.read_bytes());model=build_model(payload);expect=platform_content_expectations(payload)
    result=validate(path,expected_period=payload['period_end'],expected_strategy='iStrategy: Alpha World MS',
        ms_hkd=True,
        expected_security_axis_labels=expect['security_axis_labels'],expected_summary_table_rows=expect['summary_table_rows'],
        expected_slide_text=expect['slide_text'])
    with zipfile.ZipFile(path) as z:
        texts={i:' '.join(e.text or '' for e in ET.fromstring(z.read(f'ppt/slides/slide{i}.xml')).iter('{'+A+'}t')) for i in range(3,18)}
        verify_ms_contribution_heading(texts[10])
        joined=' '.join(texts.values())
        for term in ('USD','UST 3-month','FOCUS US Tech','NaN','undefined','null sessions',
                     'versus S&P 500 Price Index','AAPL','APPLE INC','Aug Results'):
            if term in joined:raise ValueError('Reference or unsupported text survived: '+term)
        table12=ET.fromstring(z.read('ppt/slides/slide12.xml')).find('.//{'+A+'}tbl')
        rows=[[''.join(t.text or '' for t in cell.iter('{'+A+'}t')) for cell in r.findall('{'+A+'}tc')] for r in table12.findall('{'+A+'}tr')]
        required={'Annualized return':model['risk']['annualized_return'],'Annualized volatility':model['risk']['annualized_volatility'],
            'Risk-free rate':model['risk']['risk_free_annual_rate'],'Daily VaR (95%)':model['risk']['var_95_daily'],
            'Daily Expected Tail Loss':model['risk']['cvar_95_daily'],'Win rate':model['risk']['win_rate']}
        found={r[0]:r[1] for r in rows}
        for label,value in required.items():
            if found.get(label)!=f'{value*100:.2f}%':raise ValueError('Metric label/value mismatch: '+label)
        if found.get('Sharpe ratio')!=f"{model['risk']['sharpe_ratio']:.2f}":raise ValueError('Sharpe differs')
        if '1M HIBOR' not in texts[12] or 'period average' not in texts[16]:raise ValueError('HIBOR basis missing')
        charts={}
        for n in z.namelist():
            if '/charts/' not in n or not n.endswith('.xml'):continue
            root=ET.fromstring(z.read(n));title=' '.join(e.text or '' for e in root.iter('{'+A+'}t'))
            charts[title]=root
        def chart(prefix):
            matches=[root for title,root in charts.items() if title.startswith(prefix)]
            if len(matches)!=1:raise ValueError('Required source-backed chart missing or duplicated: '+prefix)
            return matches[0]
        holdings=sorted(payload['holdings']['holdings'],key=lambda r:abs(float(r['market_value_text'])),reverse=True)[:10]
        def name(r):return ' '.join(str(r.get('description') or '').split()) or str(r.get('symbol') or 'Security').strip()
        gross=float(payload['sector_allocation']['gross_market_value'])
        verify_chart(chart('Top 10 components'),[name(r) for r in holdings],
            [round(abs(float(r['market_value_text']))/gross,6) for r in holdings],'0%')
        months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sept','Oct','Nov','Dec']
        verify_chart(chart('Rebased performance comparison'),
            [months[int(r['date'][5:7])-1]+'-'+r['date'][2:4] for r in model['month_rows']],
            [round(float(r['portfolio']),4) for r in model['month_rows']],'0',
            require_visible_marker=len(model['month_rows'])==1)
        allocation=payload['sector_allocation']['allocation']
        verify_chart(chart('Component-type weights'),[r['sector'] for r in allocation],
            [round(float(r['weight']),6) for r in allocation],'0%')
        monthly=payload['monthly_reporting'];security=monthly['security_contributions']
        detractors=sorted((r for r in security if float(r['contribution'])<0),key=lambda r:float(r['contribution']))[:5]
        contributors=sorted((r for r in security if float(r['contribution'])>0),key=lambda r:float(r['contribution']),reverse=True)[:5]
        rows=detractors+list(reversed(contributors)) or [{'symbol':'No effect','contribution':0}]
        verify_chart(chart('Top five contributors'),[name(r) for r in rows],
            [abs(round(float(r['contribution']),6)) for r in rows],'0.00%',
            [f"{float(r['contribution'])*100:.2f}%" for r in rows])
        rows=monthly['sector_contributions']
        verify_chart(chart('Component-type contribution'),[r['sector'] for r in rows],
            [abs(round(float(r['contribution']),6)) for r in rows],'0.00%',
            [f"{float(r['contribution'])*100:.2f}%" for r in rows])
        verify_chart(chart('Constant cash per initial NAV 100'),['Constant cash'],[model['constant_cash']],'"HKD "0')
        for slide in (13,16):
            if 'N/A' not in texts[slide] and 'UNAVAILABLE' not in texts[slide]:raise ValueError('Unavailable result missing')
        for key in ('expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y'):
            value=model['risk'][key]
            if (f'{value*100:.2f}%' if value is not None else 'N/A') not in texts[14]:
                raise ValueError('One-year drawdown result missing: '+key)
        if model['crisis_available']:
            expected=f"{model['risk']['crisis_maximum_drawdown']*100:.2f}%"
            if expected not in texts[14] or 'Hypothetical HKD stress' not in texts[14]:
                raise ValueError('Qualified HKD crisis metric or methodology missing')
            if '121 HKD stress sessions' not in texts[16] or 'WARN' not in texts[16]:
                raise ValueError('Crisis proxy qualification warning missing')
    return {'status':'passed','version':model['version'],'snapshot_sha256':__import__('hashlib').sha256(snapshot_path.read_bytes()).hexdigest(),
        'financial_charts_verified':6,'risk_metrics_verified':7,'qualified_crisis_verified':model['crisis_available'],
        'generic_validation':result,'fonts':fonts(path)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('deck',type=Path);parser.add_argument('snapshot',type=Path,nargs='?');args=parser.parse_args()
    print(json.dumps(audit(args.deck,args.snapshot) if args.snapshot else {'fonts':fonts(args.deck)},indent=2))
