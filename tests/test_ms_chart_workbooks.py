import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from ibkr_etl import ms_chart_workbooks as wb


def fixture():
    parts = {'[Content_Types].xml': f'<Types xmlns="{wb.CT}"/>'.encode()}
    for owner in wb.OWNERS:
        name = f'ppt/slides/charts/chart{owner}.xml'
        parts[f'ppt/slides/slide{owner}.xml'] = (
            f'<slide xmlns:c="{wb.C}" xmlns:r="{wb.R}"><c:chart r:id="rId1"/></slide>').encode()
        parts[wb.relpart(f'ppt/slides/slide{owner}.xml')] = (
            f'<Relationships xmlns="{wb.REL}"><Relationship Id="rId1" Type="{wb.R}/chart" '
            f'Target="charts/chart{owner}.xml"/></Relationships>').encode()
        parts[name] = (f'<c:chartSpace xmlns:c="{wb.C}"><c:chart><c:plotArea><c:barChart><c:ser>'
            '<c:tx><c:v>Signed return</c:v></c:tx><c:cat><c:strLit><c:ptCount val="2"/>'
            '<c:pt idx="0"><c:v>=Inert text</c:v></c:pt><c:pt idx="1"><c:v>Hong Kong &amp; China</c:v></c:pt>'
            '</c:strLit></c:cat><c:val><c:numLit><c:formatCode>0.00%</c:formatCode><c:ptCount val="2"/>'
            '<c:pt idx="0"><c:v>-0.1234</c:v></c:pt><c:pt idx="1"><c:v>0</c:v></c:pt>'
            '</c:numLit></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>').encode()
    parts['ppt/media/preserved.png'] = b'original image bytes'
    return parts


class ChartWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.pptx'
        self.output = self.root / 'output.pptx'
        self.original = fixture()
        self.source.write_bytes(wb.zipped(self.original))

    def repaired(self):
        wb.materialize(self.source, self.output)
        return wb.package(self.output.read_bytes())

    def reject(self, parts, message):
        with self.assertRaisesRegex(ValueError, message):
            wb.verify_bytes(wb.zipped(parts))

    def test_six_workbooks_match_categories_values_signs_and_headers(self):
        result = wb.materialize(self.source, self.output)
        self.assertEqual(result['embedded_workbooks_verified'], 6)
        self.assertEqual(result['converted_chart_count'], 6)
        self.assertFalse(result['source_formulas_preserved'])
        cells = wb.workbook_cells(wb.package(self.output.read_bytes())['ppt/embeddings/ms-chart-data-slide-6.xlsx'])
        self.assertEqual(cells['A2'], '=Inert text')
        self.assertEqual(cells['B2'], wb.Decimal('-0.1234'))
        self.assertEqual(cells['B3'], 0)
        self.assertEqual(wb.verify_ms_chart_workbooks(self.output)['owner_slides'], list(wb.OWNERS))
        types = wb.package(self.output.read_bytes())['[Content_Types].xml']
        self.assertIn(('xmlns="' + wb.CT + '"').encode(), types)
        self.assertNotIn(b':Default', types)

    def test_source_and_unrelated_package_parts_unchanged(self):
        before = self.source.read_bytes()
        actual = self.repaired()
        self.assertEqual(before, self.source.read_bytes())
        for name, data in self.original.items():
            if '/charts/' not in name and name != '[Content_Types].xml':
                self.assertEqual(actual[name], data)

    def test_valid_existing_workbook_preserved_byte_for_byte(self):
        self.repaired()
        destination = self.root / 'unchanged.pptx'
        result = wb.materialize(self.output, destination)
        self.assertEqual(result['converted_chart_count'], 0)
        self.assertEqual(destination.read_bytes(), self.output.read_bytes())

    def test_numeric_styles_match_cache_and_are_applied_to_cells(self):
        parts = self.repaired()
        wb.verify_bytes(self.output.read_bytes(), require_format=True)
        for owner in wb.OWNERS:
            nested = wb.package(parts[f'ppt/embeddings/ms-chart-data-slide-{owner}.xlsx'], workbook=True)
            styles = wb.xml(nested['xl/styles.xml'])
            self.assertEqual(styles.find('x:numFmts/x:numFmt', wb.NS).get('formatCode'), '0.00%')
            cells = wb.xml(nested['xl/worksheets/sheet1.xml']).findall('.//x:c', wb.NS)
            self.assertEqual([c.get('s') for c in cells], [None, None, None, '1', None, '1'])

    def test_axis_precision_and_label_formats_are_not_source_linked(self):
        parts = fixture()
        name = 'ppt/slides/charts/chart6.xml'
        parts[name] = parts[name].replace(b'</c:barChart>', b'<c:dLbls><c:showVal val="1"/></c:dLbls></c:barChart>')
        parts[name] = parts[name].replace(b'</c:plotArea>', b'<c:valAx><c:numFmt formatCode="0%"/></c:valAx></c:plotArea>')
        self.source.write_bytes(wb.zipped(parts))
        actual = self.repaired()
        chart = wb.xml(actual[name])
        axis = chart.find('.//c:valAx/c:numFmt', wb.NS)
        self.assertEqual(axis.attrib, {'formatCode': '0%', 'sourceLinked': '0'})
        labels = chart.find('.//c:dLbls', wb.NS)
        self.assertEqual(labels[0].attrib, {'formatCode': '0.00%', 'sourceLinked': '0'})
        self.assertTrue(labels[0].tag.endswith('numFmt'))

    def test_missing_numeric_style_fails(self):
        self.workbook_mutation(b's="1"', b's="0"', 'numeric cell number style differs')

    def test_substituted_style_format_fails(self):
        parts = self.repaired()
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/styles.xml'] = nested['xl/styles.xml'].replace(b'0.00%', b'0')
        parts[name] = wb.zipped(nested)
        self.reject(parts, 'number styles differ')

    def test_styles_relationship_tampering_fails(self):
        parts = self.repaired()
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/_rels/workbook.xml.rels'] = nested['xl/_rels/workbook.xml.rels'].replace(b'styles.xml', b'other.xml')
        parts[name] = wb.zipped(nested)
        self.reject(parts, 'styles relationship differs')

    def test_explicit_legacy_upgrade_preserves_all_values_and_other_parts(self):
        parts = self.repaired()
        for item in wb.verify_bytes(self.output.read_bytes())['items']:
            parts[item['workbook_part']] = wb.worksheet(['=Inert text', 'Hong Kong & China'], ['-0.1234', '0'], 'Signed return')
        legacy = self.root / 'legacy.pptx'
        raw = wb.zipped(parts)
        legacy.write_bytes(raw)
        preserved = self.root / 'legacy-preserved.pptx'
        wb.materialize(legacy, preserved)
        self.assertEqual(preserved.read_bytes(), raw)
        upgraded = self.root / 'upgraded.pptx'
        result = wb.upgrade_number_formats(legacy, upgraded)
        self.assertFalse(result['numeric_values_changed'])
        actual = wb.package(upgraded.read_bytes())
        for name in parts:
            if name not in {i['chart_part'] for i in result['items']} | {i['workbook_part'] for i in result['items']}:
                self.assertEqual(actual[name], parts[name])
        self.assertEqual(legacy.read_bytes(), raw)
        wb.verify_bytes(upgraded.read_bytes(), require_format=True)

    def test_explicit_upgrade_refuses_tampered_data_without_output(self):
        parts = self.repaired()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'-0.1234', b'0.1234')
        self.output.write_bytes(wb.zipped(parts))
        refused = self.root / 'refused.pptx'
        with self.assertRaisesRegex(ValueError, 'cells differ'):
            wb.upgrade_number_formats(self.output, refused)
        self.assertFalse(refused.exists())

    def test_file_gate_strict_mode_rejects_legacy_unstyled_snapshot(self):
        parts = self.repaired()
        for item in wb.verify_bytes(self.output.read_bytes())['items']:
            parts[item['workbook_part']] = wb.worksheet(['=Inert text', 'Hong Kong & China'], ['-0.1234', '0'], 'Signed return')
        self.output.write_bytes(wb.zipped(parts))
        self.assertEqual(wb.verify_ms_chart_workbooks(self.output)['status'], 'passed')
        with self.assertRaisesRegex(ValueError, 'number styles missing'):
            wb.verify_ms_chart_workbooks(self.output, require_format=True)

    def test_all_governed_number_formats_supported(self):
        for code in wb.FORMAT_CODES:
            raw = wb.worksheet(['value'], ['-0.1234'], 'Series', code)
            self.assertEqual(wb.workbook_cells(raw, code, require_format=True)['B2'], wb.Decimal('-0.1234'))

    def test_hkd_editor_column_is_wide_without_changing_numeric_value(self):
        raw = wb.worksheet(['Constant cash'], ['58'], 'Constant cash', '"HKD "0.00')
        parts = wb.package(raw, workbook=True)
        root = wb.xml(parts['xl/worksheets/sheet1.xml'])
        self.assertEqual(root.find('x:cols/x:col', wb.NS).attrib,
                         {'min': '2', 'max': '2', 'width': '16', 'customWidth': '1'})
        self.assertEqual(wb.workbook_cells(raw, '"HKD "0.00', require_format=True)['B2'], 58)

    def test_hkd_editor_width_required_for_new_qualification_only(self):
        raw = wb.worksheet(['Constant cash'], ['58'], 'Constant cash', '"HKD "0.00')
        parts = wb.package(raw, workbook=True)
        root = wb.xml(parts['xl/worksheets/sheet1.xml'])
        root.remove(root.find('x:cols', wb.NS))
        parts['xl/worksheets/sheet1.xml'] = wb.xml_bytes(root)
        legacy = wb.zipped(parts)
        self.assertEqual(wb.workbook_cells(legacy, '"HKD "0.00')['B2'], 58)
        with self.assertRaisesRegex(ValueError, 'editor column width'):
            wb.workbook_cells(legacy, '"HKD "0.00', require_format=True)

    def test_percentage_workbook_keeps_existing_column_geometry(self):
        raw = wb.worksheet(['value'], ['0.1234'], 'Series', '0.00%')
        parts = wb.package(raw, workbook=True)
        self.assertIsNone(wb.xml(parts['xl/worksheets/sheet1.xml']).find('x:cols', wb.NS))

    def test_unsupported_format_rejected(self):
        parts = fixture()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'0.00%', b'General')
        self.source.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'unsupported chart number format'):
            wb.materialize(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_original_or_existing_output_never_overwritten(self):
        with self.assertRaisesRegex(ValueError, 'new output'):
            wb.materialize(self.source, self.source)
        self.output.write_bytes(b'keep this')
        with self.assertRaisesRegex(ValueError, 'new output'):
            wb.materialize(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep this')

    def test_bare_charts_fail_acceptance(self):
        self.reject(self.original, 'content type missing')

    def test_missing_workbook_fails(self):
        parts = self.repaired()
        del parts['ppt/embeddings/ms-chart-data-slide-6.xlsx']
        self.reject(parts, 'embedded workbook missing')

    def workbook_mutation(self, old, new, message):
        parts = self.repaired()
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/worksheets/sheet1.xml'] = nested['xl/worksheets/sheet1.xml'].replace(old, new)
        parts[name] = wb.zipped(nested)
        self.reject(parts, message)

    def test_changed_numeric_cell_fails(self):
        self.workbook_mutation(b'-0.1234', b'0.1234', 'cells differ')

    def test_changed_category_fails(self):
        self.workbook_mutation(b'=Inert text', b'Changed label', 'cells differ')

    def test_changed_header_fails(self):
        self.workbook_mutation(b'Signed return', b'Other series', 'series header differs')

    def test_cell_formula_fails(self):
        parts = self.repaired()
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        root = wb.xml(nested['xl/worksheets/sheet1.xml'])
        ET.SubElement(root.find('.//x:c', wb.NS), '{' + wb.X + '}f').text = '1+1'
        nested['xl/worksheets/sheet1.xml'] = wb.xml_bytes(root)
        parts[name] = wb.zipped(nested)
        self.reject(parts, 'formulas are not snapshot literals')

    def test_external_relationship_fails(self):
        parts = self.repaired()
        name = wb.relpart('ppt/slides/charts/chart6.xml')
        root = wb.xml(parts[name])
        root[0].set('TargetMode', 'External')
        parts[name] = wb.xml_bytes(root)
        self.reject(parts, 'external relationship')

    def test_wrong_formula_range_fails(self):
        parts = self.repaired()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'$A$2:$A$3', b'$A$3:$A$4')
        self.reject(parts, 'wrong cell range')

    def test_duplicate_point_indices_fail(self):
        parts = self.repaired()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'idx="1"', b'idx="0"')
        self.reject(parts, 'duplicate point index')

    def test_extra_chart_fails(self):
        parts = self.repaired()
        parts['ppt/slides/charts/chart99.xml'] = parts['ppt/slides/charts/chart6.xml']
        self.reject(parts, 'extra chart')

    def test_nonfinite_numeric_values_fail_without_output(self):
        parts = fixture()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'-0.1234', b'NaN')
        self.source.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            wb.materialize(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_tampered_bound_workbook_is_never_repaired(self):
        parts = self.repaired()
        parts['ppt/slides/charts/chart6.xml'] = parts['ppt/slides/charts/chart6.xml'].replace(b'$A$2:$A$3', b'$A$3:$A$4')
        self.output.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'wrong cell range'):
            wb.materialize(self.output, self.root / 'refused.pptx')
        self.assertFalse((self.root / 'refused.pptx').exists())

    def test_duplicate_zip_parts_rejected(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('same', b'a')
            archive.writestr('same', b'b')
        with self.assertRaisesRegex(ValueError, 'duplicate package'):
            wb.package(data.getvalue())

    def test_dtd_xml_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            wb.xml(b'<!DOCTYPE foo><foo/>')

    def test_opc_root_relative_chart_target_supported_without_filesystem_access(self):
        parts = fixture()
        name = wb.relpart('ppt/slides/slide6.xml')
        parts[name] = parts[name].replace(b'Target="charts/chart6.xml"', b'Target="/ppt/slides/charts/chart6.xml"')
        self.source.write_bytes(wb.zipped(parts))
        self.assertEqual(wb.materialize(self.source, self.output)['embedded_workbooks_verified'], 6)
        with self.assertRaisesRegex(ValueError, 'escaping'):
            wb.resolve('ppt/slides/slide6.xml', '/../../etc/passwd')


if __name__ == '__main__':
    unittest.main()
