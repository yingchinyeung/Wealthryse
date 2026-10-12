import tempfile
import unittest
from pathlib import Path

from scripts.assemble_minimal_report_runtime import fingerprint_tree, report_extension_omitted


class MinimalReportRuntimeTests(unittest.TestCase):
    def test_fingerprint_binds_contents_names_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'template.pdf').write_bytes(b'example')
            first = fingerprint_tree(root)
            self.assertEqual(first['template.pdf']['bytes'], 7)
            self.assertEqual(first, fingerprint_tree(root))
            (root / 'template.pdf').write_bytes(b'changed')
            self.assertNotEqual(first, fingerprint_tree(root))
            (root / 'template.pdf').rename(root / 'different.pdf')
            self.assertNotIn('template.pdf', fingerprint_tree(root))

    def test_empty_tree_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(fingerprint_tree(Path(directory)), {})

    def test_export_retains_reporting_capabilities_and_true_provenance(self):
        source = Path('scripts/assemble_minimal_report_runtime.py').read_text()
        self.assertNotIn('database_risk_only=True', source)
        self.assertIn("'/usr/share/fonts', '/etc/fonts'", source)
        self.assertIn('selected_package_status(', source)
        self.assertIn('library_package_names(owners.get(', source)
        self.assertIn("before != fingerprint_tree(root / 'app')", source)
        self.assertIn("'lzma_included': not args.pdf_pptx_only", source)
        self.assertIn("'production_authorized': False", source)

    def test_default_keeps_lzma_and_pdf_pptx_mode_is_explicit(self):
        self.assertFalse(report_extension_omitted('_lzma.abi.so'))
        self.assertTrue(report_extension_omitted('_lzma.abi.so', pdf_pptx_only=True))
        for name in ('lzma.py', '_lzma.py', '_ssl.abi.so', '_sqlite3.abi.so', 'zlib.abi.so'):
            self.assertFalse(report_extension_omitted(name, pdf_pptx_only=True))
        for bad in (1, 0, 'true', None, []):
            with self.assertRaises(ValueError):
                report_extension_omitted('_lzma.abi.so', pdf_pptx_only=bad)


if __name__ == '__main__':
    unittest.main()
