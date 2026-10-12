"""Inspect printed T values/vectors and preserved disclosure, not metadata alone."""
from datetime import date
import hashlib
import json
import math
import re
import statistics

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase.pdfmetrics import stringWidth

from .alpha_world_ms_copy import _multiply
from .alpha_world_ms_pdf_validation import _elements
from .alpha_world_t_analytics import analyze, simulate, methodology_identity
from .alpha_world_t_factsheet import (
    AUTHORITY_SHA256, LAYOUT_VERSION, METRIC_FIELDS, METRIC_BASELINES, PREVIEW_LABEL,
    SYNTHETIC_LABEL, HEADER_VALUES, HEADER_BASELINE, HEADER_SIZE,
)


def _require(condition,message):
    if not condition: raise ValueError('T PDF accuracy check: '+message)


def _close(actual,expected):
    _require(abs(actual-expected)<.0002,'plotted coordinate differs from source value')


def _section(page,box):
    values=[]
    left,bottom,right,top=box
    def visitor(text,cm,tm,font,size):
        matrix=_multiply(tm,cm)
        if left<=matrix[4]<=right and bottom<=matrix[5]<=top and text.strip():
            values.append(text.strip().replace('\u200b',''))
    page.extract_text(visitor_text=visitor)
    return values


def _disclaimer(page):
    return _section(page,(30,80,567,536))


def _copy_identity(parts):
    # Original owner PDFs encode punctuation as separate runs, with some
    # dash/bullet glyphs missing Unicode mappings. Reflow must preserve every
    # letter, numeral and meaningful punctuation, independent of those runs.
    return re.sub(r'[\s\u200b\ufffd\u2011\u2014\-]','',''.join(parts))


def _styled_runs(page,box):
    rows=[]
    left,bottom,right,top=box
    def visitor(text,cm,tm,font,size):
        matrix=_multiply(tm,cm)
        if text.strip() and left<=matrix[4]<=right and bottom<=matrix[5]<=top:
            rows.append((text.strip(),str(font.get('/BaseFont')),round(size*math.hypot(matrix[2],matrix[3]),6),
                         round(matrix[4],5),round(matrix[5],5)))
    page.extract_text(visitor_text=visitor)
    return rows


def _validate_static_sections(reader,source):
    from .alpha_world_t_sections import load_style, STYLE_SHA256
    from .alpha_world_ms_copy import FOOTER
    style=load_style()
    _require(reader.metadata.get('/TMSStyleSHA256')==STYLE_SHA256,'MS static style identity differs')
    for i,page in enumerate(reader.pages):
        form=page['/Resources']['/XObject'].get('/TStaticMSStyle')
        _require(form is not None and form.get_object().get_data()==style.pages[i].get_contents().get_data(),
                 'MS static section content differs')
        _require(FOOTER in re.sub(r'\s+',' ',page.extract_text()),'complete MS footer wording required')
    _require(_styled_runs(reader.pages[0],(20,520,377,701))==_styled_runs(style.pages[0],(20,520,377,701)),
             'About section typography differs from MS')
    _require(_styled_runs(reader.pages[1],(16,78,582,588))==_styled_runs(style.pages[1],(16,78,582,588)),
             'T main disclaimer typography differs from approved MS v14r16')
    _require(_styled_runs(reader.pages[0],(20,731,578,750))==_styled_runs(style.pages[0],(20,731,578,750)),
             'header labels differ from the single-line MS layout')
    title=_styled_runs(reader.pages[0],(388,668,578,679))
    _require(len(title)==1 and title[0][0]=='Alpha World T Enhanced Index' and
             title[0][1].endswith('+Arial-BoldMT') and abs(title[0][2]-6.937338929)<.00001,
             'T KPI title typography differs from MS')
    _validate_kpi_title_color(reader)


def _validate_kpi_title_color(reader):
    """Check the actual title paint and source ICC profile, not metadata."""
    page=reader.pages[0];space=None;fill=None;position=None;stack=[];matches=0
    for args,op in ContentStream(page.get_contents(),reader).operations:
        if op==b'q': stack.append((space,fill,position))
        elif op==b'Q': space,fill,position=stack.pop()
        elif op==b'cs': space=args[0]
        elif op in (b'sc',b'scn'): fill=tuple(map(float,args))
        elif op in (b'rg',b'g',b'k'): space=None;fill=tuple(map(float,args))
        elif op==b'Tm': position=tuple(map(float,args[4:6]))
        elif op in (b'Tj',b'TJ') and position is not None and abs(position[0]-390.779)<.001 and abs(position[1]-672.223)<.001:
            matches+=1
            _require(fill==(0.3216,0.3373,0.3686) and space is not None,'KPI title color differs from MS')
            definition=page['/Resources']['/ColorSpace'][space].get_object()
            _require(str(definition[0])=='/ICCBased' and
                hashlib.sha256(definition[1].get_object().get_data()).hexdigest()==
                '2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e',
                'KPI title color profile differs from MS')
    _require(matches==1,'KPI title color must be verified exactly once')


def _date_headers(reader, label):
    """Check printed font, size, position and uniqueness, not PDF metadata."""
    for page_index, page in enumerate(reader.pages):
        baseline = (792., 809.)[page_index]
        runs = []
        def visitor(text, cm, tm, font, size):
            matrix = _multiply(tm, cm)
            if text.strip() and 480 < matrix[4] < 595 and baseline-10 < matrix[5] < 842:
                runs.append((text.strip(), matrix[4], matrix[5], size,
                             str((font or {}).get('/BaseFont', ''))))
        page.extract_text(visitor_text=visitor)
        expected = [('As of', 5.8, baseline+14.), (label, 8., baseline)]
        _require(len(runs) == 2, 'date header must contain exactly two text runs per page')
        for actual, (text, size, y) in zip(runs, expected):
            _require(actual[0] == text and actual[4] == '/Helvetica',
                     'date header must match MS regular Helvetica styling')
            _require(abs(actual[3]-size)<.001 and abs(actual[2]-y)<.001,
                     'date header size or vertical spacing differs from MS')
            _require(abs(actual[1]+stringWidth(text,'Helvetica',size)-572.)<.001,
                     'date header right edge differs from MS')


def _header_values(page):
    runs=[]
    def visitor(text,cm,tm,font,size):
        matrix=_multiply(tm,cm)
        if text.strip() and 707<=matrix[5]<=726:
            runs.append((text.strip(),matrix[4],matrix[5],size,
                         str((font or {}).get('/BaseFont',''))))
    page.extract_text(visitor_text=visitor)
    expected=HEADER_VALUES
    _require(len(runs)==4,'header values must appear exactly once')
    for actual,(x,text) in zip(runs,expected):
        _require(actual[0]==text and actual[4]=='/Helvetica' and abs(actual[3]-HEADER_SIZE)<.001,
                 'header values must share regular Helvetica 9.15 pt')
        _require(abs(actual[1]-x)<.001 and abs(actual[2]-HEADER_BASELINE)<.001,
                 'header values must share the approved baseline')
        _require(actual[1]+stringWidth(text,'Helvetica',HEADER_SIZE)<572.,'header values exceed the right margin')


def validate(path,snapshot,*,template_path):
    metrics,daily,evidence=analyze(snapshot)
    reader,source=PdfReader(path),PdfReader(template_path)
    _require(len(reader.pages)==2,'two pages required')
    _require(reader.metadata.get('/LayoutAuthoritySHA256')==AUTHORITY_SHA256,'authority identity differs')
    _require(reader.metadata.get('/ChartStyleVersion')==LAYOUT_VERSION,'layout identity differs')
    _require(reader.metadata.get('/PublicationAllowed')=='false','private qualification must not imply release')
    _require(json.loads(reader.metadata['/TMethodologyIdentity'])==methodology_identity(),'T methodology identity differs')
    expected_hash=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    _require(reader.metadata['/TSnapshotSHA256']==expected_hash,'frozen input identity differs')
    _require(_copy_identity(_disclaimer(reader.pages[1]))==
             _copy_identity([p.replace('\u2022','') for p in _disclaimer(source.pages[1])]),
             'T-specific main disclaimer changed')
    _validate_static_sections(reader,source)
    _require(_copy_identity(_section(reader.pages[0],(20,520,370,670)))==_copy_identity(_section(source.pages[0],(20,520,370,670))),
             'owner retained About-this-Index wording changed')
    paths,rectangles,text=_elements(reader.pages[0],reader)
    p2_paths,p2_rectangles,p2_text=_elements(reader.pages[1],reader)
    all_text='\n'.join(r[0] for r in text+p2_text)
    for stale in ('November 2022','1 Nov 2022','March 31 2026','+104.20%','1.388x','Before Interest','BioTechnolgy'):
        # The reporting date may legitimately be March 2026; do not reject it
        # just because the source example uses that month.
        if stale=='March 31 2026' and snapshot['period_end']=='2026-03-31': continue
        _require(stale not in all_text,'stale sample text remains: '+stale)
    _require([r[0] for r in text if r[0]=='January 2025']==['January 2025'],'strategy inception must be January 2025')
    _require(PREVIEW_LABEL not in all_text,'removed private preview notice remains')
    expected_synthetic = 2 if snapshot.get('synthetic_data') is True else 0
    _require(all_text.count(SYNTHETIC_LABEL)==expected_synthetic,'synthetic data labeling differs')
    _header_values(reader.pages[0])
    _require('Alpha World T Enhanced Index' in all_text,'T identity missing')
    _require('Greater China\nPortfolio' in all_text,'full Greater China sleeve missing')
    _require('Hong Kong / China /' not in all_text and 'Taiwan Portfolio' not in all_text,
             'superseded allocation caption remains')
    _require('MC Annualized Return Distribution - After Interest' in all_text,'after-interest title differs')
    _require('Initial HKD 100,000' in all_text and 'Base = 100' in all_text,'chart basis labels differ')
    period=date.fromisoformat(snapshot['period_end'])
    label=f'{period:%B} {period.day} {period.year}'
    _require(all_text.count(label)==2,'reporting date required once on each page')
    _date_headers(reader,label)
    rate='* Risk-Free Rate (1M HIBOR, period average): '+format(metrics['risk_free_annual_rate'],'.2%')
    _require([r[0] for r in text if r[0].startswith('* Risk-Free Rate')]==[rate],'risk-free label/value differs')
    for field,y in zip(METRIC_FIELDS,METRIC_BASELINES):
        value=metrics[field]
        if field in ('var_95_daily','cvar_95_daily'): value=-abs(value)
        expected=('N/A' if value is None else f'{value:.2f}' if field=='sharpe_ratio'
                  else f'{value:.2%}' if field=='win_rate' else f'{value:+.2%}')
        actual=[r[0] for r in text if abs(r[2]-y)<.001 and 500<r[1]<563 and abs(r[3]-7.)<.001]
        _require(actual==[expected],'printed KPI differs: '+field)
    rows=snapshot['performance']['series']; nav=[float(r['nav']) for r in rows]
    dates=[date.fromisoformat(r['date']) for r in rows]
    span=(dates[-1]-dates[0]).days
    lines=[p for p in paths if len(p['points'])==len(rows) and abs(p['width']-.9)<1e-6]
    _require(len(lines)==2,'both complete NAV paths required')
    for graph,(left,bottom,width,height),scale in zip(lines,((69.,367.,220.,95.),(337.,367.,219.,95.)),(100000.,100.)):
        values=[v/nav[0]*scale for v in nav]
        padding=(max(values)-min(values))*.05 or max(abs(values[0])*.05,1.)
        low,high=min(values)-padding,max(values)+padding
        for actual,day,value in zip(graph['points'],dates,values):
            _close(actual[0],left+width*(day-dates[0]).days/span)
            _close(actual[1],bottom+height*(value-low)/(high-low))
        ticks=[r for r in text if r[3]==5.5 and left-90<r[1]<left and bottom-2<=r[2]<bottom+height+8
               and re.fullmatch(r'[-\d,.]+',r[0].removeprefix('HKD '))]
        _require(bool(ticks),'numeric y ticks required')
        for label,x,y,size in ticks:
            value=float(label.removeprefix('HKD ').replace(',',''))
            _close(y+1.4,bottom+height*(value-low)/(high-low))
        ticks=[r for r in text if r[3]==5.5 and abs(r[2]-(bottom-7))<.001 and left-12<r[1]<left+width+2]
        previous_right=-math.inf
        for label,x,y,size in ticks:
            _require(x>=previous_right+2.9,'dated x labels overlap')
            previous_right=x+stringWidth(label,'Helvetica',size)
            day=date.fromisoformat(label+'-01') if len(label)==7 and label[4]=='-' else next(d for d in (dates[0],dates[-1]) if d.strftime('%d %b')==label)
            _close(x+stringWidth(label,'Helvetica',size)/2,left+width*(day-dates[0]).days/span)
    # Reconstruct exact scales from the full sample, not rounded PDF tick
    # positions: the latter amplify rounding for narrow first-month samples.
    sample,mean=simulate(daily,snapshot['period_end'])
    low,high=min(min(sample),metrics['annualized_return']),max(max(sample),metrics['annualized_return'])
    if high<=low: high=low+.01
    rough=(high-low)/6
    magnitude=10**math.floor(math.log10(rough)); residual=rough/magnitude
    step=(1 if residual<=1 else 2 if residual<=2 else 5 if residual<=5 else 10)*magnitude
    low,high=math.floor(low/step)*step,math.ceil(high/step)*step
    slope=425./(high-low)
    x_ticks=[(float(t.rstrip('%'))/100,x+stringWidth(t,'Helvetica',size)/2)
             for t,x,y,size in text if size==5.5 and abs(y-99.)<.001 and t.endswith('%')]
    _require(len(x_ticks)>=2,'distribution return ticks required')
    tick_step=.25
    first,last=math.ceil(low/tick_step-1e-9),math.floor(high/tick_step+1e-9)
    if last-first<1:
        tick_step=step
        first,last=math.ceil(low/tick_step-1e-9),math.floor(high/tick_step+1e-9)
    expected_ticks=[i*tick_step for i in range(first,last+1)]
    _require(len(x_ticks)==len(expected_ticks) and all(abs(a[0]-b)<1e-10 for a,b in zip(x_ticks,expected_ticks)),
             'distribution intermediate tick labels differ')
    for value,x in x_ticks: _close(x,114.+(value-low)*slope)
    y_ticks=[(float(t),y+1.4) for t,x,y,size in text if size==5.5 and 85<x<114 and 104<y<234
             and re.fullmatch(r'\d+(?:\.\d+)?',t)]
    _require(len(y_ticks)>=2,'distribution density ticks required')
    _require(min(sample)>=low-1e-8 and max(sample)<=high+1e-8,'histogram folds or truncates tails')
    counts=[0]*40; bin_width=(high-low)/40
    for value in sample:
        counts[min(39,max(0,int((value-low)/(high-low)*40)))]+=1
    # Reconstruct both fitted curves independently from the sampled returns.
    sigma=max(statistics.stdev(sample),1e-9)
    degenerate=statistics.variance(sample)<1e-20
    skew=statistics.fmean(((v-mean)/sigma)**3 for v in sample)
    dl,dh=0.,.995
    for _ in range(64):
        delta=(dl+dh)/2; shift=delta*math.sqrt(2/math.pi)
        if (4-math.pi)/2*shift**3/(1-shift**2)**1.5<abs(skew): dl=delta
        else: dh=delta
    delta=math.copysign((dl+dh)/2,skew)
    scale=sigma/math.sqrt(1-2*delta*delta/math.pi)
    loc=mean-scale*delta*math.sqrt(2/math.pi); shape=delta/math.sqrt(1-delta*delta)
    normal=lambda v: 0. if degenerate else math.exp(-.5*((v-mean)/sigma)**2)/(sigma*math.sqrt(2*math.pi))
    fitted=lambda v: 0. if degenerate else math.exp(-.5*((v-loc)/scale)**2)*(1+math.erf(shape*(v-loc)/scale/math.sqrt(2)))/(scale*math.sqrt(2*math.pi))
    densities=[count/(len(sample)*bin_width) for count in counts]
    grid=[low+(high-low)*i/120 for i in range(121)]
    density_high=max(max(densities),*(max(normal(v),fitted(v)) for v in grid))*1.05
    density_slope=125./density_high
    for value,y in y_ticks: _close(y,106.+value*density_slope)
    bars=[r for r in rectangles if abs(r[1]-106.)<.001 and abs(r[2]-(425./40-.4))<.001 and 114<r[0]<540]
    _require(len(bars)==40,'forty histogram bars required')
    for i,(x,y,w,h) in enumerate(bars):
        _close(x,114.+i*425./40+.2)
        _close(h,densities[i]*density_slope)
    _require(abs(sum(densities)*bin_width-1)<1e-12,'density does not integrate to one')
    curves=[p for p in paths if len(p['points'])==121 and abs(p['width']-.65)<1e-6]
    _require(len(curves)==(0 if degenerate else 2),'fitted curves differ from sample variance')
    for curve,fn,dash in zip(curves,(fitted,normal),((),(2.,1.))):
        _require(curve['dash']==dash,'fit curve and legend line styles differ')
        for i,(x,y) in enumerate(curve['points']):
            value=low+(high-low)*i/120
            _close(x,114.+425.*i/120)
            _close(y,106.+fn(value)*density_slope)
    _require(f'MC Mean: {mean:.1%}' in all_text and f"Historical: {metrics['annualized_return']:.1%}" in all_text,'distribution legend differs')
    for value,style in ((mean,(2.,1.)),(metrics['annualized_return'],(.5,1.2))):
        marker=[p for p in paths if len(p['points'])==2 and p['dash']==style and abs(p['width']-.7)<1e-6
                and abs(p['points'][0][1]-106.)<.001 and p['points'][1][1]>200]
        _require(any(abs(p['points'][0][0]-(114.+(value-low)*slope))<.0002 for p in marker),'distribution marker differs')
    children=[r for r in p2_rectangles if r[1]==612. and r[3]==55.]
    _require(children==[(x,612.,91.,55.) for x in (99.,199.5,306.,406.5)],'allocation boxes must be equal')
    for i,(child,label) in enumerate(zip((144.5,245.,351.5,452.),('91%','-49%','45%','-45%'))):
        parent=(194.75,401.75)[i//2]
        _require(any(p['points']==[(parent,710.),(child,667.)] and abs(p['width']-.6)<1e-6 for p in p2_paths),
                 'allocation connector mapping differs')
        center=(parent+child)/2+(-35. if i%2==0 else 35.)
        expected_x=center-stringWidth(label,'Helvetica-Bold',10.8)/2
        _require([t for t,x,y,size in p2_text if abs(x-expected_x)<.001 and abs(y-687.)<.001]==[label],
                 'allocation branch weight mapping differs')
    for x,heading,captions in ((144.5,'130% LONG',('Greater China','Portfolio')),
                              (245.,'70% SHORT',('Hang Seng Index',)),
                              (351.5,'150% LONG',('US Technology &','Biotechnology')),
                              (452.,'150% SHORT',('S&P500',))):
        expected_x=x-stringWidth(heading,'Helvetica-Bold',10.8)/2
        headings=[t for t,tx,y,size in p2_text if abs(tx-expected_x)<.001 and abs(y-649.)<.001]
        _require(headings==[heading],'allocation heading/branch mapping differs')
        actual=[t for t,tx,y,size in p2_text if size==9.4 and 618<=y<=641
                and abs(tx+stringWidth(t,'Helvetica',9.4)/2-x)<.001]
        _require(actual==list(captions),'allocation caption mapping differs')
    return {'status':'pass','kpis':8,'nav_paths':2,'distribution_paths':1000,'histogram_bins':40,
            't_disclaimer_preserved':True,'owner_about_copy_preserved':True,
            'disclaimer_typography_matches_ms':True,'ms_static_sections_verified':True,
            'header_labels_single_line':True,'kpi_title_color_matches_ms':True,
            'date_headers_match_ms':True,
            'header_values_aligned':True,'private_preview_notice_removed':True,
            'sample_text_removed':True,'source_data_changed':False}
