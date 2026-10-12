import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from ibkr_etl import monthly_artifacts as renderer
from ibkr_etl import alpha_world_ms_subtitle as subtitle
from test_monthly_artifacts import _ms_snapshot, _write_alpha_world_source


class MSSubtitleTests(unittest.TestCase):
    def test_shared_renderer_exact_wording_font_and_size_without_changing_input(self):
        snapshot=_ms_snapshot()
        import copy
        before=copy.deepcopy(snapshot)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf'; output=Path(folder)/'output.pdf'
            _write_alpha_world_source(source); raw=source.read_bytes()
            with patch.object(renderer,'ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256',hashlib.sha256(raw).hexdigest()):
                renderer.render_factsheet(snapshot,template_path=source,design_model_path=source,output_path=output)
            reader=PdfReader(output)
            self.assertEqual(subtitle.validate(reader.pages[0])['size'],9.025)
            self.assertEqual(reader.metadata['/MSSubtitleVersion'],subtitle.VERSION)
            self.assertNotIn(subtitle.TEXT,reader.pages[1].extract_text())
            self.assertEqual(source.read_bytes(),raw)
            self.assertEqual(snapshot,before)

    def test_validator_rejects_wrong_font_size_position_wording_or_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            for i,values in enumerate((('Helvetica',9.025,subtitle.X,subtitle.Y,subtitle.TEXT),
                ('Helvetica',8.,subtitle.X,subtitle.Y,subtitle.TEXT),
                ('Helvetica',9.025,subtitle.X,subtitle.Y-2,subtitle.TEXT),
                ('Helvetica',9.025,subtitle.X,subtitle.Y,'Multi-Tranche quantitative strategy'))):
                path=Path(folder)/f'{i}.pdf'; c=canvas.Canvas(str(path))
                f,s,x,y,t=values; c.setFont(f,s);c.drawString(x,y,t);c.save()
                with self.assertRaises(ValueError):subtitle.validate(PdfReader(path).pages[0])
            path=Path(folder)/'duplicate.pdf'; c=canvas.Canvas(str(path));c.showPage();c.save()
            p=PdfReader(path).pages[0]; subtitle.apply(p)
            with self.assertRaises(ValueError): subtitle.apply(p)

    def test_wrong_font_resource_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'empty.pdf';c=canvas.Canvas(str(path));c.showPage();c.save()
            with patch.object(subtitle,'FONT_SHA256','0'*64),self.assertRaises(ValueError):
                subtitle.apply(PdfReader(path).pages[0])


if __name__=='__main__':unittest.main()
