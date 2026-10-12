"""Frozen owner T authority and synthetic-only monthly rendering regression."""
from calendar import monthrange
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from ibkr_etl.monthly_artifacts import render_factsheet,MonthlyArtifactError
from ibkr_etl.alpha_world_t_factsheet import AUTHORITY_SHA256,LAYOUT_VERSION
from qualify_alpha_world_t_factsheet import synthetic_snapshot

SOURCE=Path(__file__).resolve().parents[1]/'templates/alpha-world-t/owner_template_20261008.pdf'


class TFactsheetTests(unittest.TestCase):
    def test_21_months_plus_future_2026_render_from_same_unchanged_authority(self):
        source_before=SOURCE.read_bytes()
        self.assertEqual(hashlib.sha256(source_before).hexdigest(),AUTHORITY_SHA256)
        with tempfile.TemporaryDirectory() as root:
            for i in range(24):
                year,month=2025+i//12,i%12+1
                day=f'{year}-{month:02d}-{monthrange(year,month)[1]:02d}'
                with self.subTest(period_end=day):
                    s=synthetic_snapshot(day); before=copy.deepcopy(s)
                    result=render_factsheet(s,template_path=SOURCE,output_path=Path(root)/f'{day}.pdf')
                    self.assertEqual(result['pdf_accuracy_validation']['status'],'pass')
                    self.assertTrue(result['pdf_accuracy_validation']['date_headers_match_ms'])
                    self.assertTrue(result['pdf_accuracy_validation']['header_values_aligned'])
                    self.assertTrue(result['pdf_accuracy_validation']['private_preview_notice_removed'])
                    self.assertTrue(result['pdf_accuracy_validation']['disclaimer_typography_matches_ms'])
                    self.assertTrue(result['pdf_accuracy_validation']['header_labels_single_line'])
                    self.assertTrue(result['pdf_accuracy_validation']['ms_static_sections_verified'])
                    self.assertTrue(result['pdf_accuracy_validation']['kpi_title_color_matches_ms'])
                    self.assertEqual(result['layout_version'],LAYOUT_VERSION)
                    self.assertEqual(result['variable_content_removed'][0]['images'],3)
                    self.assertFalse(result['publication_allowed'])
                    self.assertEqual(s,before)
                    r=PdfReader(result['output_path'])
                    self.assertEqual(len(r.pages),2)
                    self.assertEqual(r.metadata['/SyntheticData'],'true')
                    text='\n'.join(p.extract_text() for p in r.pages)
                    self.assertIn('HIBOR + Spread',text)
                    self.assertIn('Multi-Tranche (including Taiwan)',text)
                    self.assertIn('Sprout Asset',text)
                    self.assertIn('Greater China',text)
                    self.assertNotIn('Hong Kong / China /',text)
                    self.assertNotIn('PRIVATE TEMPLATE PREVIEW - NOT FOR RELEASE',text)
                    self.assertEqual(text.count('SYNTHETIC DATA'),2)
                    self.assertNotIn('1.388x',text)
                    self.assertNotIn('November 2022',text)
            self.assertEqual(SOURCE.read_bytes(),source_before)

    def test_private_gate_wrong_subject_currency_methodology_and_missing_fixing(self):
        mutations=(lambda s:s.pop('publication_allowed'),
            lambda s:s.update(publication_allowed=True),lambda s:s.update(reporting_currency='USD'),
            lambda s:s['methodology_identity'].update(contract_sha256='0'*64),
            lambda s:s['risk']['risk_free']['observations'].pop(1),
            lambda s:s['performance']['series'][0].update(nav=101))
        with tempfile.TemporaryDirectory() as root:
            for i,mutation in enumerate(mutations):
                s=synthetic_snapshot(); mutation(s); target=Path(root)/f'{i}.pdf'
                with self.assertRaises(MonthlyArtifactError): render_factsheet(s,template_path=SOURCE,output_path=target)
                self.assertFalse(target.exists())

    def test_rejects_different_authority_and_never_overwrites_source_or_existing_report(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'report.pdf'
            with self.assertRaises(MonthlyArtifactError):
                render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=SOURCE)
            with self.assertRaises(MonthlyArtifactError):
                render_factsheet(synthetic_snapshot(),template_path=Path(__file__),output_path=target)
            target.write_bytes(b'protected existing report')
            with self.assertRaises(MonthlyArtifactError):
                render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
            self.assertEqual(target.read_bytes(),b'protected existing report')

    def test_visible_wrong_kpi_or_allocation_caption_is_rejected_and_candidate_removed(self):
        import ibkr_etl.alpha_world_t_factsheet as renderer
        originals=(renderer._page_one,renderer._allocation)
        def bad_kpi(c,snapshot,inputs):
            originals[0](c,snapshot,inputs)
            c.setFont('Helvetica-Bold',7.); c.drawRightString(558.,650.5,'+99.99%')
        def bad_allocation(c):
            originals[1](c)
            c.setFont('Helvetica',9.4);c.drawCentredString(144.5,622.,'Hong Kong only')
        with tempfile.TemporaryDirectory() as root:
            for name,bad in (('_page_one',bad_kpi),('_allocation',bad_allocation)):
                target=Path(root)/f'{name}.pdf'
                with patch.object(renderer,name,side_effect=bad):
                    with self.assertRaises(ValueError): render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
                self.assertFalse(target.exists())

    def test_missing_market_dates_report_warning_without_inventing_nav(self):
        s=synthetic_snapshot(); s['performance']['series']=[r for r in s['performance']['series'] if r['date']!='2025-08-27']
        with tempfile.TemporaryDirectory() as root:
            result=render_factsheet(s,template_path=SOURCE,output_path=Path(root)/'warning.pdf')
            self.assertEqual(result['quality_status'],'coverage_warning')
            self.assertIn('Coverage warning:',PdfReader(result['output_path']).pages[0].extract_text())

    def test_date_header_rejects_bold_centered_or_wrong_spacing_on_either_page(self):
        import ibkr_etl.monthly_artifacts as artifacts
        original=artifacts._draw_alpha_world_ms_date_header
        for page_baseline in (792.,809.):
            for fault in ('bold','centered','spacing'):
                def bad_header(c,label,baseline):
                    if baseline != page_baseline:
                        return original(c,label,baseline)
                    c.setFont('Helvetica',5.8)
                    c.drawRightString(572.,baseline+(10. if fault=='spacing' else 14.),'As of')
                    c.setFont('Helvetica-Bold' if fault=='bold' else 'Helvetica',8.)
                    if fault=='centered': c.drawCentredString(530.,baseline,label)
                    else: c.drawRightString(572.,baseline,label)
                with self.subTest(page=page_baseline,fault=fault),tempfile.TemporaryDirectory() as root:
                    target=Path(root)/'invalid-header.pdf'
                    with patch.object(artifacts,'_draw_alpha_world_ms_date_header',side_effect=bad_header):
                        with self.assertRaisesRegex(ValueError,'date header'):
                            render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
                    self.assertFalse(target.exists())

    def test_header_values_reject_a_shifted_baseline_or_wrong_font(self):
        import ibkr_etl.alpha_world_t_factsheet as renderer
        for fault in ('baseline','font'):
            def bad_values(c):
                for i,(x,text) in enumerate(renderer.HEADER_VALUES):
                    c.setFont('Helvetica-Bold' if fault=='font' and i==2 else 'Helvetica',renderer.HEADER_SIZE)
                    c.drawString(x,renderer.HEADER_BASELINE+(1. if fault=='baseline' and i==2 else 0.),text)
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as root:
                target=Path(root)/'invalid-values.pdf'
                with patch.object(renderer,'_header_values',side_effect=bad_values):
                    with self.assertRaisesRegex(ValueError,'header values'):
                        render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
                self.assertFalse(target.exists())

    def test_actual_data_has_no_visible_preview_notice_but_stays_unreleased(self):
        s=synthetic_snapshot(); s['synthetic_data']=False
        with tempfile.TemporaryDirectory() as root:
            result=render_factsheet(s,template_path=SOURCE,output_path=Path(root)/'actual-label-policy.pdf')
            reader=PdfReader(result['output_path'])
            text='\n'.join(p.extract_text() for p in reader.pages)
            self.assertNotIn('PRIVATE TEMPLATE PREVIEW',text)
            self.assertNotIn('SYNTHETIC DATA',text)
            self.assertEqual(reader.metadata['/PublicationAllowed'],'false')
            self.assertFalse(result['publication_allowed'])

    def test_kpi_title_rejects_black_or_incorrect_gray(self):
        import ibkr_etl.alpha_world_t_sections as sections
        for color in ((0.,0.,0.),(.5,.5,.5)):
            with self.subTest(color=color),tempfile.TemporaryDirectory() as root:
                target=Path(root)/'wrong-title-color.pdf'
                with patch.object(sections,'KPI_TITLE_COLOR',color):
                    with self.assertRaisesRegex(ValueError,'KPI title color differs'):
                        render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
                self.assertFalse(target.exists())

    def test_regression_to_style_without_blue_kpi_divider_is_rejected(self):
        import ibkr_etl.alpha_world_t_sections as sections
        previous=PdfReader(SOURCE.parent/'ms_style_sections_v5.pdf')
        original=sections.merge_static_sections
        def missing_divider(page,style,page_number):
            original(page,style,page_number)
            if page_number==0:
                page['/Resources']['/XObject']['/TStaticMSStyle'].get_object().set_data(
                    previous.pages[0].get_contents().get_data())
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'missing-divider.pdf'
            with patch.object(sections,'merge_static_sections',side_effect=missing_divider):
                with self.assertRaisesRegex(ValueError,'MS static section content differs'):
                    render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
            self.assertFalse(target.exists())

    def test_main_disclaimer_remains_frozen_and_style_authority_is_hash_bound(self):
        import ibkr_etl.alpha_world_t_factsheet as renderer
        import ibkr_etl.alpha_world_t_sections as sections
        original=renderer._footer
        def changed_disclaimer(c,page_number,*,synthetic):
            original(c,page_number,synthetic=synthetic)
            if page_number==2:
                c.setFont('Helvetica',9); c.drawString(40,400,'Unapproved disclaimer change')
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'bad-disclaimer.pdf'
            with patch.object(renderer,'_footer',side_effect=changed_disclaimer):
                with self.assertRaisesRegex(ValueError,'disclaimer changed'):
                    render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
            self.assertFalse(target.exists())
            with patch.object(sections,'STYLE_SHA256','0'*64):
                with self.assertRaisesRegex(ValueError,'style authority hash'):
                    render_factsheet(synthetic_snapshot(),template_path=SOURCE,output_path=target)
            self.assertFalse(target.exists())


if __name__=='__main__': unittest.main()
