"""Hash-bound reusable owner T layout. Private qualification only, no publication.

Preserves the uploaded T-specific title, subtitle and main disclaimer wording.
Source dates, numbers, charts and variable glyphs are removed, not just covered.
No source NAV, position, cash, FX or fixing writes are performed here.
"""
from __future__ import annotations

from datetime import date
import hashlib
import io
import json
import math
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.lib.colors import black, white, HexColor
from reportlab.pdfgen import canvas

from .alpha_world_t_analytics import analyze, methodology_identity
from .alpha_world_ms_copy import _multiply, IDENTITY

LAYOUT_VERSION = 'alpha-world-t-owner-template-hkd-hibor-v7'
AUTHORITY_SHA256 = 'c7c3c2f199532cdfaf8c2ecac589186b1493a44d5194b97a05d40ed5a2d34291'
PREVIEW_LABEL = 'PRIVATE TEMPLATE PREVIEW - NOT FOR RELEASE'
SYNTHETIC_LABEL = 'SYNTHETIC DATA'
HEADER_VALUES = ((22.84536,'January 2025'),(154.7593,'HKD'),(299.5008,'HIBOR + Spread'),
                 (430.9917,'Multi-Tranche (including Taiwan)'))
HEADER_BASELINE = 725.86359
HEADER_SIZE = 9.15
METRIC_FIELDS = ('annualized_return', 'annualized_volatility', 'sharpe_ratio',
    'maximum_drawdown', 'var_95_daily', 'cvar_95_daily', 'win_rate', 'total_return')
METRIC_BASELINES = tuple(650.5 - i*15. for i in range(8))
REGIONS = (
    ((490.,780.,100.,43.), (20.,707.,558.,42.), (20.,519.,358.,182.),
     (386.,510.,204.,191.), (205.,485.,371.,23.),
     (20.,318.,556.,163.), (70.,65.,505.,209.), (0.,0.,595.,58.)),
    ((490.,798.,100.,40.), (95.,602.,411.,152.), (18.,78.,565.,510.), (0.,0.,595.,58.)),
)


def _inside(x, y, regions):
    return any(a <= x <= a+w and b <= y <= b+h for a,b,w,h in regions)


def _strip_variables(page, regions):
    """Remove variable text and contained chart images through nested Forms."""
    owner = page.pdf
    counts = {'text': 0, 'images': 0}
    def walk(stream, resources, transform, depth=0):
        if depth > 20:
            raise ValueError('T source form nesting exceeds limit')
        content = ContentStream(stream, owner)
        cm, tm, stack, operations = transform, IDENTITY, [], []
        for args, op in content.operations:
            if op == b'q': stack.append((cm,tm))
            elif op == b'Q': cm,tm = stack.pop()
            elif op == b'cm': cm = _multiply(tuple(map(float,args)),cm)
            elif op == b'BT': tm = IDENTITY
            elif op == b'Tm': tm = tuple(map(float,args))
            elif op in (b'Td',b'TD'):
                tm = _multiply((1.,0.,0.,1.,float(args[0]),float(args[1])),tm)
            elif op in (b'Tj',b'TJ'):
                matrix = _multiply(tm,cm)
                if _inside(matrix[4],matrix[5],regions):
                    counts['text'] += 1
                    continue
            elif op == b'Do':
                child = resources.get('/XObject',{}).get(args[0])
                if child is not None:
                    child = child.get_object()
                    if child.get('/Subtype') == '/Image':
                        corners = [(cm[4]+x*cm[0]+y*cm[2],cm[5]+x*cm[1]+y*cm[3])
                                   for x,y in ((0,0),(0,1),(1,0),(1,1))]
                        if all(_inside(x,y,regions) for x,y in corners):
                            counts['images'] += 1
                            continue
                    elif child.get('/Subtype') == '/Form':
                        walk(child,child.get('/Resources',resources),
                             _multiply(tuple(map(float,child.get('/Matrix',IDENTITY))),cm),depth+1)
            operations.append((args,op))
        content.operations = operations
        if depth == 0: page.replace_contents(content)
        else: stream.set_data(content.get_data())
    walk(page.get_contents(),page['/Resources'],IDENTITY)
    return counts


def _footer(c, page_number, *, synthetic):
    # Exact approved MS footer is supplied by the native style section asset.
    # The owner removed the visible private-preview notice. This does not grant
    # publication: the input gate, metadata and release controls remain intact.
    # Synthetic test data must still be distinguishable from actual performance.
    if synthetic:
        c.setFont('Helvetica-Bold',6.5)
        c.setFillColor(HexColor('#9b332b'))
        c.drawCentredString(297.375,51.5,SYNTHETIC_LABEL)


def _header_values(c, values=HEADER_VALUES):
    """All four values use one font and one baseline, including descenders."""
    c.setFillColor(black)
    c.setFont('Helvetica',HEADER_SIZE)
    for x,value in values:
        c.drawString(x,HEADER_BASELINE,value)


def _page_one(c, snapshot, inputs, header_values=None):
    from .monthly_artifacts import (_draw_alpha_world_line_chart,
        _draw_alpha_world_distribution, ALPHA_WORLD_FACTSHEET_LAYOUT)
    # T identity and values on the exact MS static table and row geometry.
    # A composite in the T design passes its own values; T's call is unchanged.
    if header_values is None: _header_values(c)
    else: _header_values(c, header_values)
    for field,y in zip(METRIC_FIELDS,METRIC_BASELINES):
        c.setFillColor(white); c.rect(516.,y-2.,46.,10.5,fill=1,stroke=0)
        value = inputs.chart_metrics[field]
        if field in ('var_95_daily','cvar_95_daily'): value = -abs(value)
        text = ('N/A' if value is None else f'{value:.2f}' if field=='sharpe_ratio'
                else f'{value:.2%}' if field=='win_rate' else f'{value:+.2%}')
        c.setFillColor(HexColor('#c93642' if field in ('maximum_drawdown','var_95_daily','cvar_95_daily') else '#121925' if field=='sharpe_ratio' else '#2d7168'))
        c.setFont('Helvetica-Bold',7.)
        c.drawRightString(558.,y,text)
    c.setFillColor(white); c.rect(438.,529.,124.,10.,fill=1,stroke=0)
    c.setFillColor(HexColor('#5d6878')); c.setFont('Helvetica-Oblique',4.1)
    c.drawRightString(558.,532.,'* Risk-Free Rate (1M HIBOR, period average): '
                      + format(inputs.chart_metrics['risk_free_annual_rate'],'.2%'))
    start,end = date.fromisoformat(inputs.history_start_date),date.fromisoformat(inputs.data_as_of)
    period_text = f'({start.day} {start:%b %Y} - {end.day} {end:%B %Y})'
    c.setFillColor(black); c.setFont('Helvetica',6.5)
    c.drawCentredString(260.44,494.,period_text)
    c.setFillColor(HexColor('#a9a9a9')); c.rect(321.2,497.25,251.5,.75,fill=1,stroke=0)
    layout = ALPHA_WORLD_FACTSHEET_LAYOUT['page_1']
    _draw_alpha_world_line_chart(c,inputs.normalized_nav,box=layout['capital_chart'],
        value_field='value',title='Capital Path',y_label='HKD',baseline_label='Initial HKD 100,000',
        authority_style=True,value_scale=1000.,avoid_date_label_overlap=True)
    _draw_alpha_world_line_chart(c,inputs.normalized_nav,box=layout['rebased_chart'],
        value_field='value',title='Rebased to 100',y_label='Index (Start = 100)',
        baseline_label='Base = 100',authority_style=True,avoid_date_label_overlap=True)
    _draw_alpha_world_distribution(c,inputs,inputs.chart_metrics,
        box=layout['distribution_chart'],authority_style=True)
    c.setFillColor(HexColor('#9b5d13')); c.setFont('Helvetica',5.5)
    gaps = inputs.chart_evidence['excluded_intervals']
    if gaps:
        count = sum(len(g['missing_sessions']) for g in gaps)
        c.drawString(42.,310.,f'Coverage warning: {count} missing session(s); multi-session intervals excluded from daily risk/MC. No prices imputed.')
    elif inputs.period_end != inputs.data_as_of:
        c.drawString(42.,310.,f'Month-end: {inputs.period_end}; last actual valuation: {inputs.data_as_of}. No prices imputed.')
    if inputs.chart_evidence['short_history']:
        c.setFillColor(HexColor('#5d6878')); c.setFont('Helvetica',5.5)
        c.drawString(80.,62.,'Limited history: indicative bootstrap only, not a forecast; parameter uncertainty is not shown.')


def _allocation(c):
    from .monthly_artifacts import ALPHA_WORLD_MS_ALLOCATION_LAYOUT as layout
    centers = [x+layout['child_width']/2 for x in layout['child_x']]
    parents = [(centers[i]+centers[i+1])/2 for i in (0,2)]
    c.setStrokeColor(black); c.setLineWidth(.6)
    for i,parent in enumerate(parents):
        for child in centers[i*2:i*2+2]: c.line(parent,710.,child,667.)
    c.setFillColor(black)
    for parent in parents: c.rect(parent-70.,710.,140.,41.,fill=1,stroke=0)
    for x in layout['child_x']: c.rect(x,612.,91.,55.,fill=1,stroke=0)
    c.setFillColor(white); c.setFont('Helvetica-Bold',12.)
    for center,label in zip(parents,('70%','30%')): c.drawCentredString(center,726.2,label)
    captions = (('Greater China','Portfolio'),('Hang Seng Index',),
                ('US Technology &','Biotechnology'),('S&P500',))
    for center,heading,lines in zip(centers,('130% LONG','70% SHORT','150% LONG','150% SHORT'),captions):
        c.setFont('Helvetica-Bold',10.8); c.drawCentredString(center,649.,heading)
        c.setFont('Helvetica',9.4)
        for i,text in enumerate(lines): c.drawCentredString(center,627.5+((len(lines)-1)/2-i)*11.,text)
    c.setFillColor(black); c.setFont('Helvetica-Bold',10.8)
    for i,(child,label) in enumerate(zip(centers,('91%','-49%','45%','-45%'))):
        offset = -35. if i%2==0 else 35.
        c.drawCentredString((parents[i//2]+child)/2+offset,687.,label)


def render(snapshot, *, template_path, output_path, design_model_path=None):
    from .monthly_artifacts import (FactsheetInputs, MonthlyArtifactError, _normalize_nav,
                                   _trailing_twelve_month_return,
                                   _draw_alpha_world_ms_date_header, ALPHA_WORLD_FACTSHEET_LAYOUT)
    if snapshot.get('publication_allowed') is not False:
        raise MonthlyArtifactError('T template is private-qualification only; publication is not approved')
    original = Path(template_path).read_bytes()
    if hashlib.sha256(original).hexdigest() != AUTHORITY_SHA256:
        raise MonthlyArtifactError('T source is not the uploaded owner template')
    if design_model_path is not None and hashlib.sha256(Path(design_model_path).read_bytes()).hexdigest()!=AUTHORITY_SHA256:
        raise MonthlyArtifactError('T cannot use the MS or an unrelated completed design authority')
    destination = Path(output_path)
    if destination.resolve() == Path(template_path).resolve() or destination.exists():
        raise MonthlyArtifactError('T render output must be a new file, not a source or existing report')
    try: metrics,daily,evidence = analyze(snapshot)
    except (ValueError,KeyError,TypeError) as exc: raise MonthlyArtifactError(str(exc)) from exc
    normalized = _normalize_nav(snapshot['performance']['series'])
    if not math.isclose(normalized[0]['nav'],100.,abs_tol=1e-9,rel_tol=0.):
        raise MonthlyArtifactError('T inception NAV must reconcile to the approved 100')
    if not all(math.isfinite(v) for v in metrics.values() if v is not None):
        raise MonthlyArtifactError('T metrics are not finite')
    inputs = FactsheetInputs(subject_id='alpha-world-t',reporting_currency='HKD',is_composite=True,
        history_start_date=snapshot['period_start'],period_end=snapshot['period_end'],data_as_of=snapshot['data_as_of'],
        display_date=snapshot['period_end'],nav=normalized[-1]['nav'],positions=5,gross_market_value=0.,
        top_holdings=(),sectors=(),history=(),normalized_nav=normalized,
        trailing_twelve_month_return=_trailing_twelve_month_return(normalized,snapshot['period_end']),
        chart_metrics=metrics,chart_daily_returns=daily,chart_evidence=evidence)
    source = PdfReader(io.BytesIO(original))
    if len(source.pages)!=2 or source.get_fields(): raise MonthlyArtifactError('T authority must have two static pages')
    writer,removed = PdfWriter(clone_from=source),[]
    from .alpha_world_t_sections import load_style, add_native_text, merge_static_sections, STYLE_SHA256
    style=load_style()
    for page_number,page in enumerate(writer.pages):
        removed.append(_strip_variables(page,REGIONS[page_number]))
        buffer = io.BytesIO(); c = canvas.Canvas(buffer,pagesize=(594.75,842.25))
        c.setFillColor(white)
        for region in REGIONS[page_number]:
            c.rect(*region,fill=1,stroke=0)
        c.save(); buffer.seek(0); page.merge_page(PdfReader(buffer).pages[0])
        merge_static_sections(page,style,page_number)
        buffer=io.BytesIO(); c=canvas.Canvas(buffer,pagesize=(594.75,842.25))
        period = date.fromisoformat(inputs.period_end)
        # Owner requested the exact approved MS date header on both T pages.
        # Share its renderer: regular Helvetica, common right edge and spacing.
        _draw_alpha_world_ms_date_header(c, f'{period:%B} {period.day} {period.year}',
            ALPHA_WORLD_FACTSHEET_LAYOUT[f'page_{page_number+1}']['date_y'])
        if page_number==0: _page_one(c,snapshot,inputs)
        else: _allocation(c)
        _footer(c,page_number+1,synthetic=snapshot.get('synthetic_data') is True)
        c.save(); buffer.seek(0); page.merge_page(PdfReader(buffer).pages[0])
        add_native_text(page,style,page_number)
    canonical = json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    writer.add_metadata({'/Title':f'Alpha World T Factsheet - {inputs.period_end}',
        '/Producer':'IBKR Portfolio Platform reusable T qualification renderer',
        '/LayoutFamily':'alpha-world-t-owner-template','/ChartStyleVersion':LAYOUT_VERSION,
        '/LayoutAuthoritySHA256':AUTHORITY_SHA256,'/TMSStyleSHA256':STYLE_SHA256,
        '/TMethodologyIdentity':json.dumps(methodology_identity(),sort_keys=True),
        '/TCalculationEvidence':json.dumps(evidence,sort_keys=True,allow_nan=False),
        '/TMetrics':json.dumps(metrics,sort_keys=True,allow_nan=False),
        '/TSnapshotSHA256':hashlib.sha256(canonical).hexdigest(),'/PublicationAllowed':'false',
        '/SyntheticData':str(snapshot.get('synthetic_data') is True).lower()})
    destination.parent.mkdir(parents=True,exist_ok=True)
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            writer.write(stream)
        from .alpha_world_t_pdf_validation import validate
        validation = validate(destination,snapshot,template_path=template_path)
    except Exception:
        if created: destination.unlink(missing_ok=True)
        raise
    result = {'output_path':str(destination),'pages':2,'layout_version':LAYOUT_VERSION,
        'layout_family':'alpha-world-t-owner-template','source_template_sha256':AUTHORITY_SHA256,
        'period_end':inputs.period_end,'data_as_of':inputs.data_as_of,'reporting_currency':'HKD',
        'chart_calculation':{'metrics':metrics,**evidence},'variable_content_removed':removed,
        'pdf_accuracy_validation':validation,'publication_allowed':False,
        'quality_status':'coverage_warning' if evidence['excluded_intervals'] else 'private_qualification',
        'publication_blockers':['T production history/consumer/release qualification pending',
            'Owner narrative review: supplied dynamic-rotation/below-2x copy differs from approved fixed-unit model']}
    if design_model_path is not None:
        from .monthly_artifacts import factsheet_render_contract_sha256,FACTSHEET_RENDER_CONTRACT_VERSION
        result.update(supporting_template_sha256=AUTHORITY_SHA256,completed_example_sha256=AUTHORITY_SHA256,
            render_contract_version=FACTSHEET_RENDER_CONTRACT_VERSION,
            render_contract_sha256=factsheet_render_contract_sha256(subject_id='alpha-world-t',
                supporting_template_sha256=AUTHORITY_SHA256,completed_example_sha256=AUTHORITY_SHA256))
    return result
