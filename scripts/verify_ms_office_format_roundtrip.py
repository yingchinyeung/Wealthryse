"""Read-only check of Office-saved chart values, formats and literal workbook cells.

Separate from the strict authored-snapshot validator: Office legitimately
rewrites ZIP parts, styles and numeric lexical serialization.
"""
import argparse
import hashlib
import json
from pathlib import Path

from ibkr_etl import ms_chart_workbooks as wb


def cells(raw):
    parts = wb.package(raw, workbook=True)
    for name in parts:
        if name.endswith('.rels'):
            # Includes the root .rels, whose conventional helper path is _rels/.rels.
            root = wb.xml(parts[name])
            wb.require(all(r.get('TargetMode', 'Internal') == 'Internal' for r in root), 'external Office workbook link')
    shared = []
    if 'xl/sharedStrings.xml' in parts:
        shared = [''.join(t.text or '' for t in s.iter('{' + wb.X + '}t'))
                  for s in wb.xml(parts['xl/sharedStrings.xml']).findall('x:si', wb.NS)]
    styles = wb.xml(parts['xl/styles.xml'])
    custom = {n.get('numFmtId'): n.get('formatCode') for n in styles.findall('x:numFmts/x:numFmt', wb.NS)}
    formats = {'0': 'General', '1': '0', '2': '0.00', '9': '0%', '10': '0.00%', **custom}
    xfs = styles.findall('x:cellXfs/x:xf', wb.NS)
    result = {}
    root = wb.xml(parts['xl/worksheets/sheet1.xml'])
    wb.require(root.find('.//x:f', wb.NS) is None, 'Office workbook acquired formulas')
    for cell in root.findall('x:sheetData/x:row/x:c', wb.NS):
        coord = cell.get('r')
        wb.require(coord not in result, 'duplicate Office cell')
        v = cell.find('x:v', wb.NS)
        if cell.get('t') == 's':
            value, code = shared[int(v.text)], None
        elif cell.get('t') == 'inlineStr':
            value, code = ''.join(n.text or '' for n in cell.findall('x:is/x:t', wb.NS)), None
        elif v is not None:
            wb.require(cell.get('t') in (None, 'n'), 'unsupported Office cell type')
            value = float(v.text)
            wb.require(__import__('math').isfinite(value), 'nonfinite Office value')
            style = int(cell.get('s', '0'))
            wb.require(0 <= style < len(xfs), 'invalid Office style index')
            code = formats.get(xfs[style].get('numFmtId', '0'))
        else:
            continue
        result[coord] = (value, code)
    return result


def verify(original, saved):
    wb.verify_bytes(original.read_bytes(), require_format=True)
    before, after = wb.package(original.read_bytes()), wb.package(saved.read_bytes())
    results = []
    for (owner, _, first), (last_owner, name, last) in zip(wb.charts(before), wb.charts(after)):
        wb.require(owner == last_owner, 'Office chart ownership differs')
        a, b = wb.series(first), wb.series(last)
        for tag, numeric in (('cat', False), ('val', True)):
            def cached(node):
                return [n.text for n in node.findall(f'c:{tag}//c:pt/c:v', wb.NS)]
            expected, actual = cached(a), cached(b)
            if numeric:
                expected, actual = list(map(float, expected)), list(map(float, actual))
            wb.require(expected == actual, 'Office cached values/categories differ')
        code = wb.chart_format(first)
        wb.require(wb.chart_format(last) == code, 'Office numeric cache format differs')
        def axis_formats(root):
            return [(tag, [n.attrib for n in root.findall(f'.//c:{tag}/c:numFmt', wb.NS)])
                    for tag in ('catAx', 'valAx', 'dateAx', 'serAx')]
        wb.require(axis_formats(first) == axis_formats(last), 'Office axis formats differ')
        # Office removes unused group formats when all point labels override
        # them, or labels are hidden. Check effective displayed formats rather
        # than requiring redundant XML to survive.
        def label_formats(root):
            result = []
            ser = wb.series(root)
            parents = {child: parent for parent in root.iter() for child in parent}
            chart_labels = parents[ser].find('c:dLbls', wb.NS)
            labels = ser.find('c:dLbls', wb.NS)
            if labels is None:
                labels = chart_labels
            if labels is None:
                return result
            group = labels.find('c:numFmt', wb.NS)
            show = labels.find('c:showVal', wb.NS)
            points = {int(n.find('c:idx', wb.NS).get('val')): n for n in labels.findall('c:dLbl', wb.NS)}
            for idx in range(len(ser.findall('c:val//c:pt', wb.NS))):
                point = points.get(idx)
                local_show = None if point is None else point.find('c:showVal', wb.NS)
                visible = local_show if local_show is not None else show
                rich = None if point is None else point.find('c:tx/c:rich', wb.NS)
                if rich is None and (visible is None or visible.get('val') in ('0', 'false')):
                    continue
                fmt = None if point is None else point.find('c:numFmt', wb.NS)
                if fmt is None:
                    fmt = group
                # A source-linked fallback is valid only because workbook
                # cells and cache format are independently checked below.
                result.append((idx, code if fmt is None or fmt.get('sourceLinked', '1') in ('1', 'true')
                               else fmt.get('formatCode')))
            return result
        wb.require(label_formats(first) == label_formats(last), 'Office effective label formats differ')
        external = last.find('c:externalData', wb.NS)
        rels = wb.relationships(after, name)
        binding = next(r for r in rels if r.get('Id') == external.get('{' + wb.R + '}id'))
        target = wb.resolve(name, binding.get('Target'))
        data = cells(after[target])
        if code == '"HKD "0.00':
            sheet = wb.xml(wb.package(after[target], workbook=True)['xl/worksheets/sheet1.xml'])
            widths = [n for n in sheet.findall('x:cols/x:col', wb.NS)
                      if int(n.get('min', '0')) <= 2 <= int(n.get('max', '0'))]
            wb.require(len(widths) == 1 and float(widths[0].get('width', '0')) >= 16
                       and widths[0].get('hidden', '0') not in ('1', 'true'),
                       'Office HKD editor column width lost')
        categories = [n.text for n in a.findall('c:cat//c:pt/c:v', wb.NS)]
        values = [float(n.text) for n in a.findall('c:val//c:pt/c:v', wb.NS)]
        wb.require(data['A1'][0] == 'Category' and data['B1'][0] == a.find('c:tx/c:v', wb.NS).text,
                   'Office workbook headers differ')
        wb.require(len(data) == 2 * (len(values) + 1), 'Office workbook cell count differs')
        for index, (category, value) in enumerate(zip(categories, values), 2):
            wb.require(data[f'A{index}'][0] == category and data[f'B{index}'] == (value, code),
                       'Office workbook value/category/format differs')
        # Rich signed contribution labels must not change during Office refresh.
        signed = lambda root: [''.join(t.text or '' for t in n.iter('{' + wb.A + '}t'))
                               for n in root.findall('.//c:dLbl', wb.NS)]
        wb.require(signed(first) == signed(last), 'Office signed chart labels differ')
        results.append({'owner_slide': owner, 'number_format': code, 'points': len(values),
                        'values_categories_formats_equal': True})
    return {'status': 'office_saved_chart_values_and_number_formats_passed',
            'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
            'saved_sha256': hashlib.sha256(saved.read_bytes()).hexdigest(),
            'charts_verified': len(results), 'items': results,
            'native_ui_reopen_verified_by_this_checker': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('saved', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    result = verify(args.original, args.saved)
    if args.receipt:
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result))
