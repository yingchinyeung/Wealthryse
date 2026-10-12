"""Check actual PDF vectors and printed values, not just renderer metadata.

Independent geometry reconstruction keeps mislabeled axes, stale KPI tables,
folded bootstrap tails and shifted marker lines out of private delivery too.
"""
from datetime import date
import math
import re
import statistics

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfbase.pdfmetrics import stringWidth

from .alpha_world_ms_analytics import analyze, simulate


def _require(condition, message):
    if not condition:
        raise ValueError('MS PDF accuracy check: ' + message)


def _close(actual, expected):
    _require(abs(actual-expected) <= .00015, 'plotted coordinate differs from its source value')


def _elements(page, reader):
    state = {'width':1., 'stroke':(0.,0.,0.), 'dash':()}
    stack, paths, rectangles, pending, rectangle = [], [], [], [], None
    for args, op in ContentStream(page.get_contents(), reader).operations:
        if op == b'q': stack.append(dict(state))
        elif op == b'Q': state = stack.pop()
        elif op == b'w': state['width'] = float(args[0])
        elif op == b'RG': state['stroke'] = tuple(map(float,args))
        elif op == b'd': state['dash'] = tuple(map(float,args[0]))
        elif op == b'm': pending = [tuple(map(float,args))]
        elif op == b'l': pending.append(tuple(map(float,args)))
        elif op == b're': rectangle = tuple(map(float,args))
        elif op == b'S':
            paths.append({**state,'points':pending})
            pending, rectangle = [], None
        elif op in (b'f',b'f*',b'B',b'B*',b'n'):
            if rectangle is not None and op != b'n': rectangles.append(rectangle)
            pending, rectangle = [], None
    text = []
    def visitor(value, cm, tm, font, size):
        if value.strip():
            text.append((value.strip(), tm[4]*cm[0]+tm[5]*cm[2]+cm[4],
                         tm[4]*cm[1]+tm[5]*cm[3]+cm[5], float(size)))
    page.extract_text(visitor_text=visitor)
    return paths, rectangles, text


def _step(low, high, ticks):
    rough = (high-low)/(ticks-1)
    magnitude = 10**math.floor(math.log10(rough))
    return next(v for v in (1,2,5,10) if v >= rough/magnitude)*magnitude


def validate_benchmark_typography(page):
    """Compare actual embedded glyph transforms, including nested PDF forms."""
    fields={'benchmark':[], 'risk':[]}
    identity=(1.,0.,0.,1.,0.,0.)
    def multiply(a,b):
        return (a[0]*b[0]+a[1]*b[2],a[0]*b[1]+a[1]*b[3],
            a[2]*b[0]+a[3]*b[2],a[2]*b[1]+a[3]*b[3],
            a[4]*b[0]+a[5]*b[2]+b[4],a[4]*b[1]+a[5]*b[3]+b[5])
    def walk(stream,resources,transform,field=None,depth=0):
        _require(depth<20,'benchmark form nesting exceeds limit')
        cm=transform;tm=identity;font=None;size=0.;stack=[]
        for args,op in ContentStream(stream,page.pdf).operations:
            if op==b'q':stack.append((cm,tm,font,size))
            elif op==b'Q':cm,tm,font,size=stack.pop()
            elif op==b'cm':cm=multiply(tuple(map(float,args)),cm)
            elif op==b'Tm':tm=tuple(map(float,args))
            elif op==b'Tf':
                font=str(resources['/Font'][args[0]].get_object().get('/BaseFont'));size=float(args[1])
            elif op==b'Do':
                form=resources.get('/XObject',{}).get(args[0])
                if form is None:continue
                form=form.get_object()
                if form.get('/Subtype')!='/Form':continue
                label=page.extract_xform_text(form).strip()
                child_field=field
                if label=='BENCHMARK\nHIBOR + Spread':child_field='benchmark'
                elif label=='RISK PROFILE\nMulti-Tranche':child_field='risk'
                walk(form,form.get('/Resources',resources),multiply(tuple(map(float,form.get('/Matrix',identity))),cm),child_field,depth+1)
            elif op in (b'Tj',b'TJ'):
                matrix=multiply(tm,cm);x,y=matrix[4:]
                if not 718<=y<=734.3:continue
                target='benchmark' if 296<=x<=411 else 'risk' if 430<=x<=500 else None
                if target is None:continue
                text=str(args[0]) if op==b'Tj' else ''.join(str(v) for v in args[0] if isinstance(v,str))
                expected='HIBOR + Spread' if target=='benchmark' else 'Multi-Tranche'
                _require(field==target or text==expected,'unexpected text in benchmark/risk-profile value')
                fields[target].append((font,size*math.hypot(matrix[2],matrix[3]),y))
    walk(page.get_contents(),page['/Resources'],identity)
    _require(all(fields.values()),'benchmark and risk-profile values required')
    expected=fields['risk'][0]
    for glyph in fields['benchmark']+fields['risk']:
        _require(glyph[0]==expected[0] and abs(glyph[1]-expected[1])<.0001
            and abs(glyph[2]-expected[2])<.0001,'benchmark font, size or baseline differs from Multi-Tranche')
    return {'status':'pass','font':expected[0],'font_size':expected[1],'baseline':expected[2]}


def validate_date_headers(reader, period_end):
    """Check printed font and right edge, not just date text or metadata."""
    from .alpha_world_ms_copy import _multiply
    period=date.fromisoformat(period_end)
    label=f'{period:%B} {period.day} {period.year}'
    for page,baseline in zip(reader.pages,(792.,809.)):
        runs=[]
        def visitor(text,cm,tm,font,size):
            if text.strip() in ('As of',label):
                matrix=_multiply(tm,cm)
                runs.append((text.strip(),matrix[4],matrix[5],
                             size*math.hypot(matrix[2],matrix[3]),str(font.get('/BaseFont'))))
        page.extract_text(visitor_text=visitor)
        for text,y,size in (('As of',baseline+14.,5.8),(label,baseline,8.)):
            matches=[r for r in runs if r[0]==text]
            _require(len(matches)==1,'date header must occur exactly once on each page')
            _,x,actual_y,actual_size,font=matches[0]
            _require(font=='/Helvetica','date and As of must use matching regular Helvetica')
            _close(actual_size,size);_close(actual_y,y)
            _close(x+stringWidth(text,'Helvetica',size),572.)
    return {'status':'pass','pages':2,'right_edge':572.,'font':'Helvetica',
            'label_size':5.8,'date_size':8.}


def validate(path, snapshot):
    metrics, daily, evidence = analyze(snapshot)
    reader = PdfReader(path)
    _require(len(reader.pages) == 2, 'two pages required')
    validate_date_headers(reader,snapshot['period_end'])
    paths, rectangles, text = _elements(reader.pages[0], reader)
    validate_benchmark_typography(reader.pages[0])
    from .alpha_world_ms_subtitle import validate as validate_subtitle
    validate_subtitle(reader.pages[0])
    title = [r for r in text if abs(r[2]-235)<.001 and r[3]==7.]
    expected_title = 'MC Annualized Return Distribution - After Interest'
    _require(len(title)==1 and title[0][0]==expected_title, 'NAV-after-interest distribution title differs')
    _close(title[0][1],114+425/2-stringWidth(expected_title,'Helvetica-Bold',7.)/2)
    _, _, allocation_text = _elements(reader.pages[1], reader)
    spy = [r for r in allocation_text if 406.5 <= r[1] <= 497.5 and 618 <= r[2] <= 641]
    _require(len(spy) == 1 and spy[0][0] == 'S&P500' and abs(spy[0][2]-627.5)<.001 and spy[0][3]==9.4,
             'owner-approved S&P500 allocation caption differs')
    _close(spy[0][1], 452-stringWidth('S&P500','Helvetica',9.4)/2)
    rate_label = '* Risk-Free Rate (1M HIBOR, period average): ' + format(metrics['risk_free_annual_rate'],'.2%')
    _require([r[0] for r in text if r[0].startswith('* Risk-Free Rate')] == [rate_label], 'risk-free label or rate differs')
    rows = snapshot['performance']['series']
    dates = [date.fromisoformat(r['date']) for r in rows]
    nav = [float(r['nav']) for r in rows]
    span = (dates[-1]-dates[0]).days
    lines = [p for p in paths if len(p['points']) == len(rows) and abs(p['width']-.9)<1e-6]
    _require(len(lines) == 2, 'both complete NAV paths required')
    for graph, (left,bottom,width,height), scale in zip(lines, ((69.,367.,220.,95.),(337.,367.,219.,95.)), (100000.,100.)):
        values = [v/nav[0]*scale for v in nav]
        padding = (max(values)-min(values))*.05 or max(abs(values[0])*.05,1.)
        low, high = min(values)-padding, max(values)+padding
        for actual, day, value in zip(graph['points'],dates,values):
            _close(actual[0],left+width*(day-dates[0]).days/span)
            _close(actual[1],bottom+height*(value-low)/(high-low))
        ticks = [r for r in text if r[3] == 5.5 and left-85<r[1]<left and bottom-2<=r[2]<bottom+height+8
                 and re.fullmatch(r'[-\d,.]+',r[0].removeprefix(snapshot['reporting_currency']+' '))]
        _require(bool(ticks), 'missing numeric y ticks')
        for label, x, y, size in ticks:
            value = float(label.removeprefix(snapshot['reporting_currency']+' ').replace(',',''))
            _require(low-1e-7<=value<=high+1e-7, 'y tick outside plot')
            _close(y+1.4, bottom+height*(value-low)/(high-low))
        dated = [r for r in text if r[3]==5.5 and abs(r[2]-(bottom-7))<1e-5 and left-12<r[1]<left+width+2]
        _require(bool(dated), 'missing dated x ticks')
        for label, x, y, size in dated:
            day = date.fromisoformat(label+'-01') if len(label)==7 and label[4]=='-' else next(d for d in (dates[0],dates[-1]) if d.strftime('%d %b')==label)
            _close(x+stringWidth(label,'Helvetica',size)/2, left+width*(day-dates[0]).days/span)
    fields = ('annualized_return','annualized_volatility','sharpe_ratio','maximum_drawdown','var_95_daily','cvar_95_daily','win_rate','total_return')
    for i, field in enumerate(fields):
        value = metrics[field]
        if field in {'var_95_daily','cvar_95_daily'}: value = -abs(value)
        expected = 'N/A' if value is None else f'{value:.2f}' if field=='sharpe_ratio' else f'{value:.2%}' if field=='win_rate' else f'{value:+.2%}'
        actual = [r[0] for r in text if r[3]==7. and 475<r[1]<558 and abs(r[2]-(650.5-15*i))<.001]
        _require(actual == [expected], 'printed KPI differs from NAV basis: '+field)
    sample, mean = simulate(daily, snapshot['period_end'])
    historical = metrics['annualized_return']
    low, high = min(min(sample),historical), max(max(sample),historical)
    if high<=low: high=low+.01
    step = _step(low,high,7)
    low,high = math.floor(low/step)*step,math.ceil(high/step)*step
    bin_width = (high-low)/40
    counts = [0]*40
    for value in sample:
        _require(low<=value<=high,'bootstrap tail excluded')
        counts[min(39,int((value-low)/(high-low)*40))] += 1
    densities = [count/(len(sample)*bin_width) for count in counts]
    sigma = max(statistics.stdev(sample),1e-9)
    degenerate = statistics.variance(sample) < 1e-20
    skew = statistics.fmean(((value-mean)/sigma)**3 for value in sample)
    dl,dh = 0.,.995
    for _ in range(64):
        delta=(dl+dh)/2
        shift=delta*math.sqrt(2/math.pi)
        if (4-math.pi)/2*shift**3/(1-shift**2)**1.5 < abs(skew): dl=delta
        else: dh=delta
    delta=math.copysign((dl+dh)/2,skew)
    scale=sigma/math.sqrt(1-2*delta**2/math.pi)
    loc=mean-scale*delta*math.sqrt(2/math.pi)
    shape=delta/math.sqrt(1-delta**2)
    normal=lambda x: 0. if degenerate else math.exp(-.5*((x-mean)/sigma)**2)/(sigma*math.sqrt(2*math.pi))
    fitted=lambda x: 0. if degenerate else math.exp(-.5*((x-loc)/scale)**2)*(1+math.erf(shape*(x-loc)/scale/math.sqrt(2)))/(scale*math.sqrt(2*math.pi))
    grid=[low+(high-low)*i/120 for i in range(121)]
    density_high=max(max(densities),*(max(normal(x),fitted(x)) for x in grid))*1.05
    left,bottom,width,height=114.,106.,425.,125.
    curves=[p for p in paths if len(p['points'])==121 and abs(p['width']-.65)<1e-6]
    _require(len(curves)==(0 if degenerate else 2),'fitted overlays inconsistent with sample variance')
    for curve, fn in zip(curves,(fitted,normal)):
        for actual,x in zip(curve['points'],grid):
            _close(actual[0],left+width*(x-low)/(high-low))
            _close(actual[1],bottom+height*fn(x)/density_high)
    if not degenerate:
        _require(curves[0]['dash']==() and curves[1]['dash']==(2.,1.),'fit swatches/styles mismatch')
    else:
        _require(all(any(r[0]==name for r in text) for name in ('Normal fit: N/A','Skew-normal fit: N/A')),'zero-variance fits not disclosed')
    bars=[r for r in rectangles if abs(r[1]-bottom)<1e-5 and abs(r[2]-(width/40-.4))<1e-5 and left<=r[0]<=left+width]
    _require(len(bars)==40,'all 40 histogram bins required')
    for i,(bar,density) in enumerate(zip(bars,densities)):
        _close(bar[0],left+i*width/40+.2)
        _close(bar[3],height*density/density_high)
    _require(abs(sum(densities)*bin_width-1)<1e-12,'density does not integrate to one')
    for value,dash,label in ((mean,(2.,1.),f'MC Mean: {mean:.1%}'),(historical,(.5,1.2),f'Historical: {historical:.1%}')):
        _require(any(r[0]==label for r in text),'legend number mismatch')
        markers=[p for p in paths if len(p['points'])==2 and p['dash']==dash and abs(p['width']-.7)<1e-6 and abs(p['points'][0][1]-bottom)<1e-5 and abs(p['points'][1][1]-(bottom+height))<1e-5]
        _require(len(markers)==1,'reference marker missing')
        _close(markers[0]['points'][0][0],left+width*(value-low)/(high-low))
    xticks=[r for r in text if r[3]==5.5 and abs(r[2]-(bottom-7))<1e-5 and r[0].endswith('%')]
    _require(bool(xticks),'distribution x ticks missing')
    tick_step = .25
    first, last = math.ceil(low/tick_step-1e-9), math.floor(high/tick_step+1e-9)
    if last-first < 1:
        tick_step = step  # Near-flat samples keep their original fine scale.
        first, last = math.ceil(low/tick_step-1e-9), math.floor(high/tick_step+1e-9)
    expected_ticks = [i*tick_step for i in range(first,last+1)]
    digits = max(0,-math.floor(math.log10(tick_step))-2)
    _require([r[0] for r in xticks] == [f'{v:.{digits}%}' for v in expected_ticks],
             'distribution x ticks must include every 25-percentage-point interval')
    for label,x,y,size in xticks:
        value=float(label[:-1])/100
        _require(low-1e-8<=value<=high+1e-8,'distribution x tick outside plot')
        _close(x+stringWidth(label,'Helvetica',size)/2,left+width*(value-low)/(high-low))
    guides = [p for p in paths if len(p['points'])==2 and p['dash']==(1.,1.)
              and abs(p['width']-.25)<1e-6 and abs(p['points'][0][1]-bottom)<1e-5
              and abs(p['points'][1][1]-(bottom+height))<1e-5
              and abs(p['points'][0][0]-p['points'][1][0])<1e-5]
    _require(len(guides)==len(expected_ticks),'distribution vertical guides missing')
    for guide,value in zip(guides,expected_ticks):
        _close(guide['points'][0][0],left+width*(value-low)/(high-low))
    for label,x,y,size in (r for r in text if r[3]==5.5 and left-30<r[1]<left and bottom-2<=r[2]<bottom+height+8 and re.fullmatch(r'[\d,.]+',r[0])):
        value=float(label.replace(',',''))
        _require(0<=value<=density_high+1e-8,'density y tick outside plot')
        _close(y+1.4,bottom+height*value/density_high)
    # Methodology stays in the versioned render evidence. The owner removed
    # these three explanatory blocks from the monthly factsheet design.
    all_text = '\n'.join(page.extract_text() for page in reader.pages)
    for removed in ('NAV basis:', 'Daily risk:', '1,000 bootstrap paths;',
                    'Historical marker: inception CAGR.',
                    'Inception fixed-unit structure, not month-end weights.'):
        _require(removed not in all_text, 'retired methodology note reappeared: '+removed)
    if evidence['excluded_intervals']:
        _require(any('Coverage warning:' in r[0] for r in text),'missing data coverage warning')
    return {'status':'pass','line_points':2*len(rows),'histogram_bins':40,'curve_points':0 if degenerate else 242,'kpis':8,'source_value_sha256':evidence['source_value_sha256']}
