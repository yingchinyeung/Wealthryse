import io
import unittest

from pypdf import PdfReader
from reportlab.pdfgen import canvas
from ibkr_etl import monthly_artifacts as renderer
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers
import test_alpha_world_ms_pdf_accuracy as accuracy_tests


class MSDateHeaderTests(unittest.TestCase):
    def test_both_pages_all_months_share_regular_font_and_right_edge(self):
        for month in range(1,13):
            end=f'2026-{month:02d}-01'
            stream=io.BytesIO();c=canvas.Canvas(stream)
            for y in (792.,809.):
                renderer._draw_alpha_world_ms_date_header(c,renderer._alpha_world_display_date(end),y)
                c.showPage()
            c.save()
            proof=validate_date_headers(PdfReader(stream),end)
            self.assertEqual(proof['font'],'Helvetica')
            self.assertEqual(proof['right_edge'],572.)

    def test_old_centered_serif_date_is_rejected(self):
        stream=io.BytesIO();c=canvas.Canvas(stream)
        for y in (792.,809.):
            c.setFont('Helvetica',5.8);c.drawCentredString(540.,y+14,'As of')
            c.setFont('Times-Bold',8.);c.drawCentredString(540.,y,'September 30 2026')
            c.showPage()
        c.save()
        with self.assertRaises(ValueError):validate_date_headers(PdfReader(stream),'2026-09-30')

    def test_duplicate_shifted_or_wrong_font_header_fails_delivery(self):
        original=renderer._draw_alpha_world_ms_date_header
        for wrong in ('duplicate','shift','font'):
            def faulty(c,text,y):
                if wrong=='shift':original(c,text,y+1.)
                else:
                    original(c,text,y)
                    c.setFont('Times-Bold' if wrong=='font' else 'Helvetica',8.)
                    c.drawRightString(572.,y,text)
            accuracy_tests.MSPDFAccuracyTests()._reject('_draw_alpha_world_ms_date_header',faulty)


if __name__=='__main__':unittest.main()
