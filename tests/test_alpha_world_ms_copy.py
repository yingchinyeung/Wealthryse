import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from ibkr_etl import alpha_world_ms_copy as copy
from ibkr_etl import monthly_artifacts as renderer
from test_monthly_artifacts import _ms_snapshot, _write_alpha_world_source


class MSDisclaimerCopyTests(unittest.TestCase):
    def test_renderer_uses_corrected_copy_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.pdf'; output = Path(directory)/'output.pdf'
            _write_alpha_world_source(model); before = model.read_bytes()
            with patch.object(renderer, 'ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256', hashlib.sha256(before).hexdigest()):
                renderer.render_factsheet(_ms_snapshot(), template_path=model, design_model_path=model, output_path=output)
            self.assertEqual(model.read_bytes(), before)
            self.assertEqual(copy.validate_copy(output)['status'], 'pass')
            reader = PdfReader(output)
            self.assertEqual(reader.metadata['/MSCopyVersion'], copy.COPY_VERSION)
            text = '\n'.join(p.extract_text() for p in reader.pages)
            for typo in ('Thisdocument', 'providedfor', 'theWealthRyse'):
                self.assertNotIn(typo, text)
            self.assertIn('S&P500', text)
            self.assertIn('Sprout Asset Management (Asia) Limited acts as the Index Advisor.',text)
            self.assertNotIn('recognized benchmarks such as MSCI',text)
            self.assertIn('investment decisions; and',text)

    def test_supplied_copy_matches_T_words_and_font_is_portable(self):
        from ibkr_etl.alpha_world_t_pdf_validation import _disclaimer,_copy_identity
        root=Path(__file__).resolve().parents[1]
        t=PdfReader(root/'templates/alpha-world-t/owner_template_20261008.pdf')
        expected=copy.PARAGRAPHS[:6]+copy.BULLETS+copy.PARAGRAPHS[6:]
        # T encodes list dots as text; MS retains its vector bullet typography.
        t_words=[part.replace('\u2022','') for part in _disclaimer(t.pages[1])]
        self.assertEqual(_copy_identity(expected),_copy_identity(t_words))
        path=root/'src/ibkr_etl/assets/alpha_world_ms_disclaimer_font.pdf'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),copy.FONT_SHA256)
        font=copy._EmbeddedFont(PdfReader(path).pages[0]['/Resources']['/Font']['/MSDisclaimerGeorgia'])
        self.assertEqual(font.code_bytes,1)
        self.assertTrue(all(c in font.codes for c in ''.join(expected)))
        self.assertTrue(font.width('Sprout Asset Management (Asia) Limited',9)>0)

    def test_missing_regions_fail_closed(self):
        writer = PdfWriter()
        for _ in range(2): writer.add_blank_page(width=595.5, height=842.25)
        with self.assertRaisesRegex(ValueError, 'font/region not found'):
            copy.repair_disclaimers(writer.pages)

    def test_validator_rejects_missing_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.pdf'; _write_alpha_world_source(model)
            with self.assertRaisesRegex(ValueError, 'footer copy differs'):
                copy.validate_copy(model)

    def test_word_wrap_rejects_an_unfittable_word(self):
        class Font:
            def width(self, text, size): return len(text)
        with self.assertRaisesRegex(ValueError, 'cannot fit'):
            copy._wrap('unfittable', Font(), 1, 3)


if __name__ == '__main__': unittest.main()
