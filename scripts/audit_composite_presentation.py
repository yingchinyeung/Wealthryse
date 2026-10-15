"""Independent exported-package checks for a composite's deck in the Alpha World MS or T design.

The MS checks (audit_alpha_ms_presentation) on the composite's own model,
identity and sleeves, plus its slide 18 and the speaker notes.
"""
import argparse
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

from audit_alpha_ms_presentation import A, C, fonts, verify_chart, verify_ms_contribution_heading
from ibkr_etl.composite_presentation import build_model, verify_page
from ibkr_etl.presentation_design import platform_content_expectations
from validate_monthly_presentation import validate

# Reference-account, MS/T and unsupported text; a composite may use a word only where its own identity does.
FOREIGN = ('USD', 'Alpha World MS', 'Alpha World T', 'UST 3-month', 'FOCUS US Tech', 'NaN', 'undefined', 'null sessions',
           'versus S&P 500 Price Index', 'AAPL', 'APPLE INC', 'Aug Results', 'Frongello', 'U16088571')


def _text(root):
    return ' '.join(e.text or '' for e in root.iter('{' + A + '}t'))


# The renderer formats with JavaScript's toFixed: half away from zero on the exact value, never a sign on zero.
def js_text(value, digits):
    value = float(value)
    text = format(Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), 'f')
    return text.lstrip('-') if value == 0 else text


def js_fixed(value, digits):
    """The renderer's round(value, digits)."""
    return float(js_text(value, digits))


def js_percent(value, digits=1):
    """The renderer's percent(value, digits)."""
    return js_text(float(value) * 100, digits) + '%'


def monthly_table(model, period_end):
    """Slide 15's two year rows as the adapted renderer writes them: the composite's own years."""
    first = max(int(model['identity']['inception'][:4]), int(period_end[:4]) - 1)
    rows = {year: ['—'] * 12 for year in (first, first + 1)}
    for row in model['month_rows']:
        year, month = int(row['date'][:4]), int(row['date'][5:7])
        if year in rows:
            rows[year][month - 1] = js_percent(row['portfolio_monthly_return'])
    return [[str(year), *cells] for year, cells in rows.items()]


def _table(root):
    return [[''.join(t.text or '' for t in cell.iter('{' + A + '}t')) for cell in row.findall('{' + A + '}tc')]
            for row in root.findall('{' + A + '}tr')]


def _axis_limit(root, limit):
    found = root.find('.//{' + C + '}valAx/{' + C + '}scaling/{' + C + '}' + limit)
    return None if found is None else float(found.get('val'))


def audit(path, snapshot_path):
    raw = snapshot_path.read_bytes()
    payload = json.loads(raw)
    model = build_model(payload)
    identity = model['identity']
    expect = platform_content_expectations(payload)
    result = validate(path, expected_period=payload['period_end'], expected_strategy=identity['strategy_name'],
                      composite_design=True, expected_security_axis_labels=expect['security_axis_labels'],
                      expected_summary_table_rows=expect['summary_table_rows'], expected_slide_text=expect['slide_text'])
    own = ' '.join([identity['display_name'], identity['description'],
                    *(str(r.get('description') or '') for r in payload['holdings']['holdings'])])
    foreign = [term for term in FOREIGN if term not in own]
    foreign_notes = [term for term in (*FOREIGN, 'S&P 500', 'Yahoo') if term not in own]
    with zipfile.ZipFile(path) as z:
        texts = {i: _text(ET.fromstring(z.read(f'ppt/slides/slide{i}.xml'))) for i in range(1, 21)}
        verify_ms_contribution_heading(texts[10])
        joined = ' '.join(texts[i] for i in range(1, 18))
        for term in foreign:
            if term in joined:
                raise ValueError('Reference or unsupported text survived: ' + term)
        notes = [_text(ET.fromstring(z.read(n))) for n in z.namelist() if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', n)]
        if len(notes) != 20 or any(identity['speaker_note'] not in note for note in notes):
            raise ValueError("Every slide's notes must describe the composite")
        if any(term in note for note in notes for term in foreign_notes):
            raise ValueError("The reference account's speaker notes survived")
        found = {r[0]: r[1] for r in _table(ET.fromstring(z.read('ppt/slides/slide12.xml')).find('.//{' + A + '}tbl'))}
        risk = model['risk']
        for label, key in (('Annualized return', 'annualized_return'), ('Annualized volatility', 'annualized_volatility'),
                           ('Risk-free rate', 'risk_free_annual_rate'), ('Daily VaR (95%)', 'var_95_daily'),
                           ('Daily Expected Tail Loss', 'cvar_95_daily'), ('Win rate', 'win_rate')):
            if found.get(label) != js_percent(risk[key], 2):
                raise ValueError('Metric label/value mismatch: ' + label)
        if found.get('Sharpe ratio') != js_text(risk['sharpe_ratio'], 2):
            raise ValueError('Sharpe differs')
        if '1M HIBOR' not in texts[12] or 'period average' not in texts[16]:
            raise ValueError('HIBOR basis missing')
        if 'Fixed-unit contribution to return' not in texts[9] or identity['inception_note'] not in texts[15]:
            raise ValueError("The composite's contribution basis or inception is missing")
        years = [_table(t) for t in ET.fromstring(z.read('ppt/slides/slide15.xml')).iter('{' + A + '}tbl')]
        years = [rows for rows in years if rows and rows[0][:1] == ['Year']]
        if len(years) != 1 or years[0][1:] != monthly_table(model, payload['period_end']):
            raise ValueError("Slide 15's monthly returns are not the composite's own years")
        charts = {}
        for n in z.namelist():
            if '/charts/' in n and n.endswith('.xml'):
                root = ET.fromstring(z.read(n))
                charts[_text(root)] = root

        def chart(prefix):
            matches = [root for title, root in charts.items() if title.startswith(prefix)]
            if len(matches) != 1:
                raise ValueError('Required source-backed chart missing or duplicated: ' + prefix)
            return matches[0]

        def name(r):
            return ' '.join(str(r.get('description') or '').split()) or str(r.get('symbol') or 'Security').strip()

        holdings = sorted(payload['holdings']['holdings'], key=lambda r: abs(float(r['market_value_text'])),
                          reverse=True)[:10]
        gross = float(payload['sector_allocation']['gross_market_value'])
        verify_chart(chart('Top 10 components'), [name(r) for r in holdings],
                     [js_fixed(abs(float(r['market_value_text'])) / gross, 6) for r in holdings], '0%')
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec']
        verify_chart(chart('Rebased performance comparison'),
                     [months[int(r['date'][5:7]) - 1] + '-' + r['date'][2:4] for r in model['month_rows']],
                     [js_fixed(r['portfolio'], 4) for r in model['month_rows']], '0',
                     require_visible_marker=len(model['month_rows']) == 1)
        allocation = payload['sector_allocation']['allocation']
        weights = chart('Component-type weights')
        verify_chart(weights, [r['sector'] for r in allocation], [js_fixed(r['weight'], 6) for r in allocation], '0%')
        high = _axis_limit(weights, 'max')
        if high is not None and high < max(float(r['weight']) for r in allocation):
            raise ValueError('A component-type weight runs past its chart axis')
        monthly = payload['monthly_reporting']
        security = monthly['security_contributions']
        detractors = sorted((r for r in security if float(r['contribution']) < 0), key=lambda r: float(r['contribution']))[:5]
        contributors = sorted((r for r in security if float(r['contribution']) > 0), key=lambda r: float(r['contribution']),
                              reverse=True)[:5]
        rows = detractors + list(reversed(contributors)) or [{'symbol': 'No effect', 'contribution': 0}]
        verify_chart(chart('Top five contributors'), [name(r) for r in rows],
                     [abs(js_fixed(r['contribution'], 6)) for r in rows], '0.00%',
                     [js_percent(r['contribution'], 2) for r in rows])
        rows = monthly['sector_contributions']
        verify_chart(chart('Component-type contribution'), [r['sector'] for r in rows],
                     [abs(js_fixed(r['contribution'], 6)) for r in rows], '0.00%',
                     [js_percent(r['contribution'], 2) for r in rows])
        cash = chart('Constant cash per initial NAV 100')
        verify_chart(cash, ['Constant cash'], [js_fixed(model['constant_cash'], 2)], '"HKD "0')
        low = _axis_limit(cash, 'min')
        if low is not None and low > model['constant_cash']:
            raise ValueError("Borrowed cash falls below its chart's axis")
        for slide in (13, 16):
            if 'N/A' not in texts[slide] and 'UNAVAILABLE' not in texts[slide]:
                raise ValueError('Unavailable result missing')
        for key in ('expected_maximum_drawdown_1y', 'actual_maximum_drawdown_1y', 'tail_maximum_drawdown_1y'):
            value = risk[key]
            if (js_percent(value, 2) if value is not None else 'N/A') not in texts[14]:
                raise ValueError('One-year drawdown result missing: ' + key)
        if model['crisis_available']:
            if ('Hypothetical HKD stress' not in texts[14]
                    or js_percent(risk['crisis_maximum_drawdown'], 2) not in texts[14]):
                raise ValueError("The composite's crisis replay result is missing")
        elif 'Hypothetical HKD stress' in texts[14] or 'Crisis replay unavailable' not in texts[14]:
            raise ValueError('Crisis replay must show as unavailable without its qualified inputs')
    page = verify_page(path, model)
    return {'status': 'passed', 'version': model['version'], 'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
            'financial_charts_verified': 6, 'risk_metrics_verified': 7, 'composite_page': page,
            'speaker_notes_verified': 20, 'generic_validation': result, 'fonts': fonts(path)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('deck', type=Path)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.deck, args.snapshot), indent=2))
