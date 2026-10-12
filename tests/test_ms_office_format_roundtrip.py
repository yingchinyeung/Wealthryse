from pathlib import Path
import tempfile
import unittest

from ibkr_etl import ms_chart_workbooks as wb
from test_ms_chart_workbooks import fixture
from verify_ms_office_format_roundtrip import verify


class OfficeFormatRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'literal.pptx'
        parts = fixture()
        for name in list(parts):
            if name.endswith('.xml') and '/charts/' in name:
                parts[name] = parts[name].replace(b'</c:plotArea>',
                    b'<c:valAx><c:numFmt formatCode="0%"/></c:valAx></c:plotArea>')
        source.write_bytes(wb.zipped(parts))
        self.original = self.root / 'original.pptx'
        wb.materialize(source, self.original)
        self.saved = self.root / 'saved.pptx'
        self.saved.write_bytes(self.original.read_bytes())

    def test_all_six_chart_values_and_workbook_formats_equal(self):
        self.assertEqual(verify(self.original, self.saved)['charts_verified'], 6)

    def test_numeric_cache_change_rejected(self):
        parts = wb.package(self.saved.read_bytes())
        name = 'ppt/slides/charts/chart6.xml'
        parts[name] = parts[name].replace(b'-0.1234', b'0.1234')
        self.saved.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'cached values'):
            verify(self.original, self.saved)

    def test_axis_precision_change_rejected(self):
        parts = wb.package(self.saved.read_bytes())
        name = 'ppt/slides/charts/chart6.xml'
        parts[name] = parts[name].replace(b'formatCode="0%"', b'formatCode="0.00%"')
        self.saved.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'axis formats'):
            verify(self.original, self.saved)

    def test_workbook_format_loss_rejected(self):
        parts = wb.package(self.saved.read_bytes())
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/styles.xml'] = nested['xl/styles.xml'].replace(b'formatCode="0.00%"', b'formatCode="General"')
        parts[name] = wb.zipped(nested)
        self.saved.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'value/category/format'):
            verify(self.original, self.saved)

    def test_office_long_numeric_lexical_serialization_is_not_false_financial_failure(self):
        parts = wb.package(self.saved.read_bytes())
        name = 'ppt/embeddings/ms-chart-data-slide-6.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/worksheets/sheet1.xml'] = nested['xl/worksheets/sheet1.xml'].replace(b'-0.1234', b'-0.1234000000000000000')
        parts[name] = wb.zipped(nested)
        self.saved.write_bytes(wb.zipped(parts))
        self.assertEqual(verify(self.original, self.saved)['charts_verified'], 6)

    def hkd_fixture(self):
        source = self.root / 'hkd-literal.pptx'
        parts = fixture()
        name = 'ppt/slides/charts/chart11.xml'
        parts[name] = parts[name].replace(b'0.00%', b'&quot;HKD &quot;0.00')
        source.write_bytes(wb.zipped(parts))
        original = self.root / 'hkd-original.pptx'
        saved = self.root / 'hkd-saved.pptx'
        wb.materialize(source, original)
        saved.write_bytes(original.read_bytes())
        return original, saved

    def test_cash_column_width_survives_roundtrip(self):
        original, saved = self.hkd_fixture()
        self.assertEqual(verify(original, saved)['charts_verified'], 6)

    def test_lost_cash_column_width_rejected(self):
        original, saved = self.hkd_fixture()
        parts = wb.package(saved.read_bytes())
        name = 'ppt/embeddings/ms-chart-data-slide-11.xlsx'
        nested = wb.package(parts[name], workbook=True)
        nested['xl/worksheets/sheet1.xml'] = nested['xl/worksheets/sheet1.xml'].replace(b'width="16"', b'width="8"')
        parts[name] = wb.zipped(nested)
        saved.write_bytes(wb.zipped(parts))
        with self.assertRaisesRegex(ValueError, 'editor column width lost'):
            verify(original, saved)


if __name__ == '__main__':
    unittest.main()
