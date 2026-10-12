"""MS-only geometry changes; no financial, caption or T layout changes."""
import io
import copy
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

from ibkr_etl import monthly_artifacts as r
from test_monthly_artifacts import _ms_snapshot, _snapshot
import test_alpha_world_ms_pdf_accuracy as accuracy_tests


def overlay(snapshot):
    stream=io.BytesIO();c=canvas.Canvas(stream,pagesize=(595.276,841.89))
    r._draw_alpha_world_page_two(c,r.factsheet_inputs(snapshot),595.276,841.89)
    c.save()
    return PdfReader(io.BytesIO(stream.getvalue())).pages[0]


class MSAllocationLayoutTests(unittest.TestCase):
    def test_uniform_boxes_cover_whole_old_diagram_and_preserve_inputs(self):
        snapshot=_ms_snapshot();before=copy.deepcopy(snapshot);page=overlay(snapshot)
        operations=ContentStream(page.get_contents(),page.pdf).operations
        rects=[tuple(map(float,args)) for args,op in operations if op==b're']
        self.assertEqual(rects[1],(95,607,410,148))
        self.assertEqual(rects[2:4],[(124.75,710,140,41),(331.75,710,140,41)])
        self.assertEqual(rects[4:],[(x,612,91,55) for x in (99,199.5,306,406.5)])
        self.assertEqual(snapshot,before)
        r._validate_alpha_world_ms_allocation(page)

    def test_caption_centers_branch_labels_and_connectors_are_symmetric(self):
        page=overlay(_ms_snapshot());runs=r._collect_text_runs(page)
        headings=('130% LONG','70% SHORT','150% LONG','150% SHORT')
        for center,label in zip((144.5,245,351.5,452),headings):
            matches=[run for run in runs if run[0]==label]
            self.assertEqual(len(matches),1)
            _,x,y,size,font=matches[0]
            self.assertAlmostEqual(x+stringWidth(label,'Helvetica-Bold',10.8)/2,center,places=5)
            self.assertEqual((y,size,font),(649,10.8,'/Helvetica-Bold'))
        captions=(('China & Hong Kong',633),('Portfolio',622),('Hang Seng Index',627.5),
            ('US Technology &',633),('BioTechnology',622),('S&P500',627.5))
        for label,y in captions:
            self.assertEqual([run[2] for run in runs if run[0]==label],[y])
        for center,label in zip((134.625,254.875,341.625,461.875),('91%','-49%','45%','-45%')):
            run=next(run for run in runs if run[0]==label)
            self.assertAlmostEqual(run[1]+stringWidth(label,'Helvetica-Bold',10.8)/2,center,places=5)
            self.assertEqual(run[2],687)
        operations=ContentStream(page.get_contents(),page.pdf).operations
        moves=[tuple(map(float,args)) for args,op in operations if op==b'm']
        ends=[tuple(map(float,args)) for args,op in operations if op==b'l']
        self.assertEqual(moves,[(194.75,710)]*2+[(401.75,710)]*2)
        self.assertEqual(ends,[(x,667) for x in (144.5,245,351.5,452)])

    def test_t_does_not_use_ms_allocation_redraw(self):
        snapshot=_snapshot();snapshot['subject_id']='alpha-world-t'
        with patch.object(r,'_draw_alpha_world_ms_allocation') as redraw:
            page=overlay(snapshot)
        redraw.assert_not_called()
        self.assertIn('Greater China',page.extract_text())

    def test_nonuniform_box_rejected_even_with_current_layout_metadata(self):
        original=r._draw_alpha_world_ms_allocation
        def wrong(c):
            original(c)
            c.rect(406.5,612,100,55,fill=1,stroke=0)
        accuracy_tests.MSPDFAccuracyTests()._reject('_draw_alpha_world_ms_allocation',wrong)

    def test_shifted_caption_rejected_even_with_current_layout_metadata(self):
        original=r._draw_alpha_world_ms_allocation
        def wrong(c):
            original(c)
            c.setFont('Helvetica',9.4);c.drawCentredString(445,630,'S&P500')
        accuracy_tests.MSPDFAccuracyTests()._reject('_draw_alpha_world_ms_allocation',wrong)


if __name__=='__main__':unittest.main()
