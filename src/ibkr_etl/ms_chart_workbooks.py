"""MS-only, bounded OOXML chart-data snapshots and independent cell verification.

The six MS charts are authored from complete literal, frozen snapshot data.
These XLSX files contain those literal values, not recovered source formulas.
No desktop skill/runtime, Excel library, network access or source writes needed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
X = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
OWNERS = (6, 7, 8, 9, 10, 11)
MAX_PACKAGE = 256 * 1024 * 1024
MAX_PART = 32 * 1024 * 1024
MAX_POINTS = 10000
NS = {'c': C, 'r': R, 'rel': REL, 'x': X, 'ct': CT}
FORMAT_CODES = {'0', '0.0%', '0.00%', '"HKD "0.00'}
STYLE_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml'


def require(condition, message):
    if not condition:
        raise ValueError('MS chart workbook: ' + message)


def xml(data):
    require(0 < len(data) <= MAX_PART and b'<!DOCTYPE' not in data.upper()
            and b'<!ENTITY' not in data.upper(), 'unsafe or excessive XML')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('MS chart workbook: malformed XML') from error


def xml_bytes(root):
    # Office's package readers expect the content-type/relationship namespace
    # as the default, including unprefixed children. Match the approved packages.
    for namespace in (CT, REL, X):
        if root.tag.startswith('{' + namespace + '}'):
            ET.register_namespace('', namespace)
            break
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def package(data, *, workbook=False):
    limit = 4 * 1024 * 1024 if workbook else MAX_PACKAGE
    require(0 < len(data) <= limit, 'package size limit exceeded')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            require(0 < len(infos) <= (16 if workbook else 2000), 'package part count')
            names = [i.filename for i in infos]
            require(len(set(names)) == len(names), 'duplicate package parts')
            require(all(n and not n.startswith('/') and '\\' not in n
                        and not any(p in ('', '.', '..') for p in n.split('/')) for n in names),
                    'unsafe package path')
            require(sum(i.file_size for i in infos) <= limit
                    and all(i.file_size <= MAX_PART and not i.flag_bits & 1 for i in infos),
                    'expanded package limit or encrypted part')
            return {i.filename: archive.read(i) for i in infos}
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError('MS chart workbook: invalid ZIP') from error


def zipped(parts):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    return result.getvalue()


def relpart(part):
    return posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')


def relationships(parts, part):
    root = xml(parts[relpart(part)]) if relpart(part) in parts else ET.Element('{' + REL + '}Relationships')
    require(root.tag == '{' + REL + '}Relationships', 'wrong relationship root')
    rows = list(root)
    ids = [r.get('Id') for r in rows]
    require(all(r.tag == '{' + REL + '}Relationship' for r in rows)
            and all(ids) and len(set(ids)) == len(ids), 'duplicate or invalid relationship IDs')
    for row in rows:
        require(row.get('TargetMode', 'Internal') == 'Internal', 'external relationship forbidden')
    return root


def resolve(part, target):
    require(bool(target) and '\\' not in target and ':' not in target
            and not target.startswith('//') and '?' not in target and '#' not in target,
            'invalid internal target')
    if target.startswith('/'):
        # OPC root-relative part URI, never a filesystem path or URL.
        require('..' not in target.split('/'), 'escaping relationship target')
        name = posixpath.normpath(target[1:])
    else:
        name = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
    require(not name.startswith('../') and name != '..', 'escaping relationship target')
    return name


def charts(parts):
    rows = []
    for slide in OWNERS:
        name = f'ppt/slides/slide{slide}.xml'
        require(name in parts, 'missing chart owner slide')
        refs = xml(parts[name]).findall('.//c:chart', NS)
        require(len(refs) == 1, 'exactly one chart per approved owner required')
        rels = relationships(parts, name)
        matches = [r for r in rels if r.get('Id') == refs[0].get('{' + R + '}id')
                   and r.get('Type') == R + '/chart']
        require(len(matches) == 1, 'chart owner binding missing')
        target = resolve(name, matches[0].get('Target'))
        require(target in parts and re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml', target),
                'invalid chart part')
        rows.append((slide, target, xml(parts[target])))
    actual = {n for n in parts if re.fullmatch(r'ppt/(?:slides/)?charts/chart\d+\.xml', n)}
    require(len({row[1] for row in rows}) == 6 and actual == {row[1] for row in rows},
            'duplicate or extra chart parts')
    return rows


def number(text):
    require(isinstance(text, str) and 0 < len(text) <= 100, 'missing numeric value')
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError('MS chart workbook: invalid numeric value') from error
    require(value.is_finite() and len(value.normalize().as_tuple().digits) <= 15
            and (value == 0 or -307 <= value.adjusted() <= 307), 'nonfinite or non-Excel numeric value')
    return value


def points(root, *, numeric):
    count = root.find('c:ptCount', NS)
    require(count is not None and (count.get('val') or '').isdigit(), 'missing point count')
    size = int(count.get('val'))
    require(0 < size <= MAX_POINTS, 'point count limit')
    nodes = root.findall('c:pt', NS)
    require(len(nodes) == size, 'incomplete chart data')
    result = {}
    for point in nodes:
        index = point.get('idx', '')
        require(index.isdigit() and int(index) < size and int(index) not in result, 'duplicate point index')
        values = point.findall('c:v', NS)
        require(len(values) == 1 and values[0].text is not None, 'missing point value')
        value = values[0].text
        if numeric:
            number(value)
        else:
            require(0 < len(value) <= 2000, 'category text limit')
        result[int(index)] = value
    return [result[i] for i in range(size)]


def series(root):
    found = root.findall('.//c:ser', NS)
    require(len(found) == 1 and root.find('.//c:scatterChart', NS) is None
            and root.find('.//c:bubbleChart', NS) is None, 'unsupported MS chart family or series count')
    return found[0]


def chart_format(root):
    formats = series(root).findall('c:val/c:numRef/c:numCache/c:formatCode', NS)
    if not formats:
        formats = series(root).findall('c:val/c:numLit/c:formatCode', NS)
    require(len(formats) == 1 and formats[0].text in FORMAT_CODES, 'unsupported chart number format')
    return formats[0].text


def number_styles(format_code):
    require(format_code in FORMAT_CODES, 'unsupported workbook number format')
    root = ET.Element('{' + X + '}styleSheet')
    formats = ET.SubElement(root, '{' + X + '}numFmts', count='1')
    ET.SubElement(formats, '{' + X + '}numFmt', numFmtId='164', formatCode=format_code)
    font = ET.SubElement(ET.SubElement(root, '{' + X + '}fonts', count='1'), '{' + X + '}font')
    ET.SubElement(font, '{' + X + '}sz', val='11')
    ET.SubElement(font, '{' + X + '}name', val='Calibri')
    fills = ET.SubElement(root, '{' + X + '}fills', count='2')
    for pattern in ('none', 'gray125'):
        ET.SubElement(ET.SubElement(fills, '{' + X + '}fill'), '{' + X + '}patternFill', patternType=pattern)
    border = ET.SubElement(ET.SubElement(root, '{' + X + '}borders', count='1'), '{' + X + '}border')
    for edge in ('left', 'right', 'top', 'bottom', 'diagonal'):
        ET.SubElement(border, '{' + X + '}' + edge)
    base = {'numFmtId': '0', 'fontId': '0', 'fillId': '0', 'borderId': '0'}
    ET.SubElement(ET.SubElement(root, '{' + X + '}cellStyleXfs', count='1'), '{' + X + '}xf', base)
    xfs = ET.SubElement(root, '{' + X + '}cellXfs', count='2')
    ET.SubElement(xfs, '{' + X + '}xf', {**base, 'xfId': '0'})
    ET.SubElement(xfs, '{' + X + '}xf', {**base, 'numFmtId': '164', 'xfId': '0', 'applyNumberFormat': '1'})
    ET.SubElement(ET.SubElement(root, '{' + X + '}cellStyles', count='1'), '{' + X + '}cellStyle',
                  name='Normal', xfId='0', builtinId='0')
    return root


def fix_chart_formats(root):
    format_code = chart_format(root)
    # Retain the template's axis precision, independently of workbook cells.
    for node in root.findall('.//c:numFmt', NS):
        node.set('sourceLinked', '0')
    for labels in root.findall('.//c:dLbls', NS) + root.findall('.//c:dLbl', NS):
        if labels.find('c:numFmt', NS) is None:
            # CT_DLbl(s): numFmt follows idx/delete/layout/tx and precedes spPr.
            prior = {'{' + C + '}' + t for t in ('dLbl', 'idx', 'delete', 'layout', 'tx')}
            index = next((i for i, child in enumerate(labels) if child.tag not in prior), len(labels))
            labels.insert(index, ET.Element('{' + C + '}numFmt', formatCode=format_code, sourceLinked='0'))
    return format_code


def worksheet(categories, values, title, format_code=None):
    root = ET.Element('{' + X + '}worksheet')
    if format_code == '"HKD "0.00':
        # Excel otherwise replaces the formatted HKD amount with #### in its
        # default-width column. This affects the editor only, not chart data.
        columns = ET.SubElement(root, '{' + X + '}cols')
        ET.SubElement(columns, '{' + X + '}col', min='2', max='2',
                      width='16', customWidth='1')
    data = ET.SubElement(root, '{' + X + '}sheetData')
    for index, pair in enumerate([('Category', title), *zip(categories, values)], 1):
        row = ET.SubElement(data, '{' + X + '}row', r=str(index))
        for column, value in zip(('A', 'B'), pair):
            cell = ET.SubElement(row, '{' + X + '}c', r=f'{column}{index}')
            if column == 'A' or index == 1:
                cell.set('t', 'inlineStr')
                text = ET.SubElement(ET.SubElement(cell, '{' + X + '}is'), '{' + X + '}t')
                text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                text.text = value
            else:
                number(value)
                if format_code is not None:
                    cell.set('s', '1')
                ET.SubElement(cell, '{' + X + '}v').text = value
    workbook = ET.Element('{' + X + '}workbook')
    sheet = ET.SubElement(ET.SubElement(workbook, '{' + X + '}sheets'), '{' + X + '}sheet',
                          name='Chart Data', sheetId='1')
    sheet.set('{' + R + '}id', 'rId1')
    types = ET.Element('{' + CT + '}Types')
    ET.SubElement(types, '{' + CT + '}Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(types, '{' + CT + '}Default', Extension='xml', ContentType='application/xml')
    content_types = [
        ('/xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),
        ('/xl/worksheets/sheet1.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')]
    if format_code is not None:
        content_types.append(('/xl/styles.xml', STYLE_MIME))
    for name, content_type in content_types:
        ET.SubElement(types, '{' + CT + '}Override', PartName=name, ContentType=content_type)
    def rel(kind, target):
        out = ET.Element('{' + REL + '}Relationships')
        ET.SubElement(out, '{' + REL + '}Relationship', Id='rId1', Type=R + '/' + kind, Target=target)
        return xml_bytes(out)
    parts = {'[Content_Types].xml': xml_bytes(types), '_rels/.rels': rel('officeDocument', 'xl/workbook.xml'),
                   'xl/workbook.xml': xml_bytes(workbook), 'xl/_rels/workbook.xml.rels': rel('worksheet', 'worksheets/sheet1.xml'),
                   'xl/worksheets/sheet1.xml': xml_bytes(root)}
    if format_code is not None:
        rels = xml(parts['xl/_rels/workbook.xml.rels'])
        ET.SubElement(rels, '{' + REL + '}Relationship', Id='rId2', Type=R + '/styles', Target='styles.xml')
        parts['xl/_rels/workbook.xml.rels'] = xml_bytes(rels)
        parts['xl/styles.xml'] = xml_bytes(number_styles(format_code))
    return zipped(parts)


def workbook_cells(raw, expected_format=None, *, require_format=False):
    parts = package(raw, workbook=True)
    styled = 'xl/styles.xml' in parts
    required = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml',
                'xl/_rels/workbook.xml.rels', 'xl/worksheets/sheet1.xml'}
    if styled:
        required.add('xl/styles.xml')
    require(set(parts) == required,
            'unsupported workbook snapshot structure or external links')
    require(not require_format or styled, 'workbook number styles missing')
    if styled:
        style = xml(parts['xl/styles.xml'])
        formats = style.findall('x:numFmts/x:numFmt', NS)
        require(len(formats) == 1, 'workbook number styles differ')
        code = formats[0].get('formatCode')
        require(code in FORMAT_CODES and (expected_format is None or code == expected_format)
                and ET.tostring(style) == ET.tostring(number_styles(code)), 'workbook number styles differ')
    types = xml(parts['[Content_Types].xml'])
    overrides = types.findall('ct:Override', NS)
    expected_types = {
        '/xl/workbook.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml',
        '/xl/worksheets/sheet1.xml': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'}
    if styled:
        expected_types['/xl/styles.xml'] = STYLE_MIME
    require(len(overrides) == len(expected_types) and {e.get('PartName'): e.get('ContentType') for e in overrides} == expected_types,
        'wrong workbook content types')
    for name, kind, target in (('', 'officeDocument', 'xl/workbook.xml'),
                               ('xl/workbook.xml', 'worksheet', 'worksheets/sheet1.xml')):
        rels = relationships(parts, name)
        require(len(rels) == (2 if styled and name else 1) and rels[0].get('Id') == 'rId1'
                and rels[0].get('Type') == R + '/' + kind and rels[0].get('Target') == target,
                'workbook worksheet relationship differs')
        if styled and name:
            require(rels[1].attrib == {'Id': 'rId2', 'Type': R + '/styles', 'Target': 'styles.xml'},
                    'workbook styles relationship differs')
    sheets = xml(parts['xl/workbook.xml']).findall('x:sheets/x:sheet', NS)
    require(len(sheets) == 1 and sheets[0].get('name') == 'Chart Data'
            and sheets[0].get('{' + R + '}id') == 'rId1', 'wrong worksheet identity')
    root = xml(parts['xl/worksheets/sheet1.xml'])
    require(root.tag == '{' + X + '}worksheet' and root.find('.//x:f', NS) is None,
            'workbook formulas are not snapshot literals')
    if require_format and expected_format == '"HKD "0.00':
        columns = root.findall('x:cols/x:col', NS)
        require(len(columns) == 1 and columns[0].attrib == {
            'min': '2', 'max': '2', 'width': '16', 'customWidth': '1'},
            'HKD editor column width missing or invalid')
    cells = {}
    for cell in root.findall('x:sheetData/x:row/x:c', NS):
        coordinate = cell.get('r', '')
        require(re.fullmatch(r'[AB][1-9]\d{0,4}', coordinate) and coordinate not in cells, 'invalid or duplicate cell')
        if cell.get('t') == 'inlineStr':
            require(cell.get('s') in (None, '0'), 'text cell number style differs')
            texts = cell.findall('x:is/x:t', NS)
            require(len(texts) == 1 and texts[0].text is not None, 'missing inline cell text')
            value = texts[0].text
        else:
            require(cell.get('s') == ('1' if styled else None), 'numeric cell number style differs')
            require(cell.get('t') in (None, 'n'), 'unsupported cell type')
            values = cell.findall('x:v', NS)
            require(len(values) == 1, 'missing cell value')
            value = number(values[0].text)
        cells[coordinate] = value
    require(len(cells) <= (MAX_POINTS + 1) * 2, 'cell count limit')
    return cells


def verify_bytes(raw, *, require_format=False):
    parts = package(raw)
    types = xml(parts['[Content_Types].xml'])
    defaults = [e for e in types if e.tag == '{' + CT + '}Default' and e.get('Extension') == 'xlsx']
    require(len(defaults) == 1 and defaults[0].get('ContentType') == MIME, 'workbook content type missing')
    rows = []
    for owner, name, root in charts(parts):
        external = root.findall('c:externalData', NS)
        require(len(external) == 1, 'missing embedded workbook binding')
        rels = relationships(parts, name)
        matches = [r for r in rels if r.get('Id') == external[0].get('{' + R + '}id') and r.get('Type') == R + '/package']
        require(len(matches) == 1, 'missing embedded workbook relationship')
        target = resolve(name, matches[0].get('Target'))
        require(target in parts and re.fullmatch(r'ppt/embeddings/[^/]+\.xlsx', target), 'embedded workbook missing')
        format_code = chart_format(root)
        cells = workbook_cells(parts[target], format_code, require_format=require_format)
        if require_format:
            require(all(n.get('sourceLinked') == '0' for n in root.findall('.//c:numFmt', NS)),
                    'chart number format remains source-linked')
            require(all(n.find('c:numFmt', NS) is not None for n in
                        root.findall('.//c:dLbls', NS) + root.findall('.//c:dLbl', NS)),
                    'chart label number format missing')
        node = series(root)
        title = node.find('c:tx/c:v', NS)
        require(title is not None and title.text == cells.get('B1') and cells.get('A1') == 'Category', 'series header differs')
        pair = []
        for tag, ref, cache, col, numeric in (('cat', 'strRef', 'strCache', 'A', False),
                                              ('val', 'numRef', 'numCache', 'B', True)):
            parent = node.find('c:' + tag, NS)
            require(parent is not None and len(parent) == 1 and parent[0].tag == '{' + C + '}' + ref,
                    'unverified chart data reference')
            reference = parent[0]
            cached = reference.find('c:' + cache, NS)
            require(cached is not None, 'chart reference cache missing')
            values = points(cached, numeric=numeric)
            formula = reference.find('c:f', NS)
            require(formula is not None and formula.text == f"'Chart Data'!${col}$2:${col}${len(values)+1}",
                    'unsupported or wrong cell range')
            actual = [cells.get(f'{col}{i+2}') for i in range(len(values))]
            expected = [number(v) for v in values] if numeric else values
            require(actual == expected, 'workbook cells differ from chart cache')
            pair.append(values)
        require(len(pair[0]) == len(pair[1]) and len(cells) == 2 * (len(pair[0]) + 1), 'worksheet/category/value counts differ')
        rows.append({'owner_slide': owner, 'chart_part': name, 'workbook_part': target,
                     'number_format': format_code,
                     'workbook_sha256': hashlib.sha256(parts[target]).hexdigest(), 'points_verified': len(pair[0])})
    require(len({r['workbook_part'] for r in rows}) == 6, 'chart workbooks are not individually bound')
    return {'status': 'passed', 'embedded_workbooks_verified': 6, 'owner_slides': list(OWNERS),
            'output_sha256': hashlib.sha256(raw).hexdigest(), 'items': rows}


def verify_ms_chart_workbooks(path, *, require_format=False):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_PACKAGE,
            'invalid or excessive presentation file')
    return verify_bytes(path.read_bytes(), require_format=require_format)


def materialize(source, destination):
    source, destination = Path(source), Path(destination)
    require(source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= MAX_PACKAGE,
            'invalid or excessive presentation file')
    require(source.resolve() != destination.resolve() and not destination.exists() and not destination.is_symlink(),
            'new output path required')
    raw = source.read_bytes()
    parts = package(raw)
    rows = charts(parts)
    existing = [r.find('c:externalData', NS) is not None for _, _, r in rows]
    if all(existing):
        verification = verify_bytes(raw)
        with destination.open('xb') as stream:
            stream.write(raw)
        return {**verification, 'converted_chart_count': 0, 'source_workbook_preserved': True}
    require(not any(existing), 'mixed literal and bound MS charts cannot be repaired implicitly')
    ET.register_namespace('c', C)
    ET.register_namespace('a', A)
    ET.register_namespace('r', R)
    for owner, name, root in rows:
        node = series(root)
        title = node.find('c:tx/c:v', NS)
        require(title is not None and title.text, 'literal series title missing')
        values = []
        for tag, kind, col, numeric in (('cat', 'strLit', 'A', False), ('val', 'numLit', 'B', True)):
            parent = node.find('c:' + tag, NS)
            require(parent is not None and len(parent) == 1 and parent[0].tag == '{' + C + '}' + kind,
                    'only complete literal MS charts may create snapshot workbooks')
            literal = parent[0]
            data = points(literal, numeric=numeric)
            reference = ET.Element('{' + C + '}' + ('numRef' if numeric else 'strRef'))
            ET.SubElement(reference, '{' + C + '}f').text = f"'Chart Data'!${col}$2:${col}${len(data)+1}"
            cache = ET.SubElement(reference, '{' + C + '}' + ('numCache' if numeric else 'strCache'))
            for child in literal:
                cache.append(deepcopy(child))
            parent.remove(literal)
            parent.append(reference)
            values.append(data)
        require(len(values[0]) == len(values[1]), 'literal category/value lengths differ')
        target = f'ppt/embeddings/ms-chart-data-slide-{owner}.xlsx'
        require(target not in parts, 'snapshot workbook path already exists')
        parts[target] = worksheet(*values, title.text, fix_chart_formats(root))
        rels = relationships(parts, name)
        require(not any(r.get('Type') == R + '/package' or r.get('Id') == 'rIdMSChartData' for r in rels),
                'unbound source workbook cannot be replaced')
        ET.SubElement(rels, '{' + REL + '}Relationship', Id='rIdMSChartData', Type=R + '/package',
                      Target=posixpath.relpath(target, posixpath.dirname(name)))
        parts[relpart(name)] = xml_bytes(rels)
        external = ET.Element('{' + C + '}externalData', {'{' + R + '}id': 'rIdMSChartData'})
        ET.SubElement(external, '{' + C + '}autoUpdate', val='0')
        index = next((i for i, child in enumerate(root) if child.tag in
                      {'{' + C + '}' + t for t in ('printSettings', 'userShapes', 'extLst')}), len(root))
        root.insert(index, external)
        parts[name] = xml_bytes(root)
    types = xml(parts['[Content_Types].xml'])
    defaults = [e for e in types if e.get('Extension') == 'xlsx']
    require(not defaults or len(defaults) == 1 and defaults[0].get('ContentType') == MIME, 'conflicting workbook content type')
    if not defaults:
        ET.SubElement(types, '{' + CT + '}Default', Extension='xlsx', ContentType=MIME)
        parts['[Content_Types].xml'] = xml_bytes(types)
    result = zipped(parts)
    verified = verify_bytes(result, require_format=True)
    require(source.read_bytes() == raw, 'source changed during repair')
    with destination.open('xb') as stream:
        stream.write(result)
    return {**verified, 'converted_chart_count': 6, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'source_workbook_preserved': False, 'source_formulas_preserved': False,
            'conversion': 'literal_frozen_chart_data_snapshot'}


def upgrade_number_formats(source, destination):
    """Explicit, fail-closed upgrade of our verified literal snapshot workbooks.

    Never reconstruct arbitrary workbooks, formulas, edited or tampered data.
    Default materialize still preserves valid bound files byte for byte.
    """
    source, destination = Path(source), Path(destination)
    require(source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= MAX_PACKAGE,
            'invalid or excessive presentation file')
    require(source.resolve() != destination.resolve() and not destination.exists() and not destination.is_symlink(),
            'new output path required')
    raw = source.read_bytes()
    before = verify_bytes(raw)
    parts = package(raw)
    ET.register_namespace('c', C)
    ET.register_namespace('a', A)
    ET.register_namespace('r', R)
    for item, (_, name, root) in zip(before['items'], charts(parts)):
        node = series(root)
        categories = points(node.find('c:cat/c:strRef/c:strCache', NS), numeric=False)
        values = points(node.find('c:val/c:numRef/c:numCache', NS), numeric=True)
        title = node.find('c:tx/c:v', NS).text
        parts[item['workbook_part']] = worksheet(categories, values, title, fix_chart_formats(root))
        parts[name] = xml_bytes(root)
    result = zipped(parts)
    verified = verify_bytes(result, require_format=True)
    require(source.read_bytes() == raw, 'source changed during format upgrade')
    with destination.open('xb') as stream:
        stream.write(result)
    return {**verified, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'conversion': 'verified_snapshot_number_format_upgrade', 'numeric_values_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path, nargs='?')
    parser.add_argument('--upgrade-number-formats', action='store_true')
    args = parser.parse_args()
    if args.upgrade_number_formats and not args.destination:
        parser.error('format upgrade requires a new output path')
    print(json.dumps((upgrade_number_formats if args.upgrade_number_formats else materialize)(args.source, args.destination) if args.destination
                     else verify_ms_chart_workbooks(args.source), allow_nan=False))


if __name__ == '__main__':
    main()
