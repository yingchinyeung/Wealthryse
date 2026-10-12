"""Portable release tests; no dependency on the unpublished T implementation."""
import hashlib
from pathlib import Path
import unittest
from pypdf import PdfReader
from ibkr_etl import alpha_world_ms_copy as copy
from ibkr_etl.monthly_artifacts import factsheet_layout_version


class OwnerDisclaimerReleaseTests(unittest.TestCase):
    def test_exact_accepted_copy(self):
        self.assertEqual(copy.COPY_SHA256,'ee5ad73440fc5fa0ae5b80c577dcfabc76fe0f44706c7d320979d723a60032ee')
        self.assertIn('Sprout Asset Management (Asia) Limited acts as the Index Advisor.', ' '.join(copy.PARAGRAPHS))
        self.assertTrue(copy.BULLETS[3].endswith('; and'))

    def test_portable_georgia_covers_all_owner_words(self):
        path=Path(copy.__file__).parent/'assets/alpha_world_ms_disclaimer_font.pdf'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),copy.FONT_SHA256)
        font=copy._EmbeddedFont(PdfReader(path).pages[0]['/Resources']['/Font']['/MSDisclaimerGeorgia'])
        self.assertEqual(font.code_bytes,1)
        self.assertTrue(all(c in font.codes for c in ''.join(copy.PARAGRAPHS+copy.BULLETS)))
        self.assertGreater(font.width('Sprout Asset Management (Asia) Limited',9),0)

    def test_layout_is_subject_scoped(self):
        self.assertEqual(factsheet_layout_version('alpha-world-ms'),'subject-completed-factsheet-model-v14r16-alpha-world-ms-owner-disclaimer')
        self.assertNotIn('v14r16',factsheet_layout_version('unrelated-subject'))
