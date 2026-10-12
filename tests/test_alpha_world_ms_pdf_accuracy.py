import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import monthly_artifacts as renderer
from test_monthly_artifacts import _ms_snapshot, _write_alpha_world_source


class MSPDFAccuracyTests(unittest.TestCase):
    def test_owner_removed_notes_absent_but_internal_methodology_preserved(self):
        from pypdf import PdfReader
        snapshot = _ms_snapshot()
        unchanged = copy.deepcopy(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            model, output = Path(directory)/'model.pdf', Path(directory)/'output.pdf'
            _write_alpha_world_source(model)
            with patch.object(renderer,'ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256',hashlib.sha256(model.read_bytes()).hexdigest()):
                result = renderer.render_factsheet(snapshot,template_path=model,design_model_path=model,output_path=output)
            text = '\n'.join(page.extract_text() for page in PdfReader(output).pages)
            self.assertIn('HIBOR + Spread',text)
            self.assertNotIn('S&P 500 Price Index',text)
            self.assertNotIn('SPY (S&P 500 ETF)',text)
            self.assertNotIn('Unadjusted price',text)
            self.assertIn('S&P500',text)
            self.assertIn('MC Annualized Return Distribution - After Interest',text)
            self.assertNotIn('MC One-Year Return Distribution',text)
            for removed in ('NAV basis:', 'Daily risk:', '1,000 bootstrap paths;',
                            'Historical marker: inception CAGR.',
                            'Inception fixed-unit structure, not month-end weights.'):
                self.assertNotIn(removed,text)
            evidence = result['chart_calculation']
            self.assertEqual(evidence['simulation_paths'],1000)
            self.assertEqual(evidence['simulation_horizon_sessions'],252)
            self.assertIn('growth_annualization',evidence)
            self.assertEqual(evidence['simulation_interest'],
                'Actual effects present in NAV only; no projected financing or cash interest')
            self.assertEqual(result['pdf_accuracy_validation']['status'],'pass')
            self.assertEqual(snapshot,unchanged)

    def test_rejects_reintroduced_owner_removed_note(self):
        original = renderer._draw_alpha_world_page_two
        def stale(c, *args):
            original(c, *args)
            c.drawString(25,765,'Inception fixed-unit structure, not month-end weights.')
        self._reject('_draw_alpha_world_page_two', stale)

    def test_rejects_stale_benchmark_label(self):
        original = renderer._draw_alpha_world_page_one
        def stale(c, *args):
            original(c, *args)
            c.setFont('Helvetica',8.)
            c.drawString(299,724.8,'S&P 500 Price Index')
        self._reject('_draw_alpha_world_page_one', stale)

    def test_rejects_benchmark_font_different_from_risk_profile(self):
        original=renderer._draw_alpha_world_page_one
        def wrong(c,*args):
            original(c,*args)
            c.setFont('Times-Roman',9.15)
            c.drawString(299.4,724.8,'HIBOR + Spread')
        self._reject('_draw_alpha_world_page_one',wrong)

    def test_rejects_smaller_benchmark_font(self):
        original=renderer._draw_alpha_world_page_one
        def wrong(c,*args):
            original(c,*args)
            c.setFont('Helvetica',8.)
            c.drawString(299.4,724.8,'HIBOR + Spread')
        self._reject('_draw_alpha_world_page_one',wrong)

    def test_rejects_shifted_benchmark_baseline(self):
        original=renderer._draw_alpha_world_page_one
        def wrong(c,*args):
            original(c,*args)
            c.setFont('Helvetica',9.15)
            c.drawString(299.4,725.8,'HIBOR + Spread')
        self._reject('_draw_alpha_world_page_one',wrong)

    def test_rejects_stale_spy_allocation_caption(self):
        original = renderer._draw_alpha_world_page_two
        def stale(c, *args):
            original(c, *args)
            c.setFont('Helvetica',8.)
            c.drawCentredString(445,622,'Unadjusted price')
        self._reject('_draw_alpha_world_page_two', stale)

    def test_rejects_previous_spy_only_allocation_caption(self):
        original = renderer._draw_alpha_world_page_two
        def stale(c, *args):
            original(c, *args)
            c.setFont('Helvetica',8.)
            c.drawCentredString(445,634,'SPY')
        self._reject('_draw_alpha_world_page_two', stale)

    def test_rejects_stale_distribution_title(self):
        original = renderer._draw_alpha_world_distribution
        def stale(c, *args, **kwargs):
            original(c, *args, **kwargs)
            c.setFont('Helvetica-Bold',7.)
            c.drawCentredString(326.5,235,'MC One-Year Return Distribution')
        self._reject('_draw_alpha_world_distribution', stale)

    def test_rejects_missing_intermediate_distribution_ticks(self):
        original = renderer._alpha_world_ms_distribution_ticks
        def incomplete(*args):
            ticks, step = original(*args)
            return ticks[::2], step
        self._reject('_alpha_world_ms_distribution_ticks', incomplete)

    def test_flat_history_renders_without_inventing_fits_or_sharpe(self):
        from pypdf import PdfReader
        snapshot = _ms_snapshot()
        for row in snapshot['performance']['series']: row['nav'] = 100.
        with tempfile.TemporaryDirectory() as directory:
            model, output = Path(directory)/'model.pdf', Path(directory)/'output.pdf'
            _write_alpha_world_source(model)
            with patch.object(renderer,'ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256',hashlib.sha256(model.read_bytes()).hexdigest()):
                renderer.render_factsheet(snapshot,template_path=model,design_model_path=model,output_path=output)
            text = PdfReader(output).pages[0].extract_text()
            self.assertIn('Normal fit: N/A',text)
            self.assertIn('Skew-normal fit: N/A',text)

    def test_axis_zero_never_has_negative_zero_label(self):
        self.assertEqual(renderer._chart_numeric_label(-1e-16,.2),'0.0')
    def _reject(self, helper, replacement):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.pdf'
            output = Path(directory)/'output.pdf'
            _write_alpha_world_source(model)
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            with patch.object(renderer,'ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256',digest), patch.object(renderer,helper,side_effect=replacement):
                with self.assertRaises(renderer.MonthlyArtifactError):
                    renderer.render_factsheet(_ms_snapshot(), template_path=model, design_model_path=model, output_path=output)
            self.assertFalse(output.exists(), 'A numerically wrong PDF must not survive delivery validation')

    def test_rejects_shifted_line_with_correct_version_metadata(self):
        original = renderer._draw_alpha_world_line_chart
        def shifted(c, rows, **kwargs):
            rows = copy.deepcopy(rows)
            rows[3]['value'] += .01
            return original(c, rows, **kwargs)
        self._reject('_draw_alpha_world_line_chart', shifted)

    def test_rejects_stale_printed_kpi_with_correct_version_metadata(self):
        original = renderer._draw_alpha_world_page_one
        def stale(c, snapshot, inputs, *args):
            inputs.chart_metrics['total_return'] += .02
            return original(c, snapshot, inputs, *args)
        self._reject('_draw_alpha_world_page_one', stale)

    def test_rejects_wrong_bootstrap_with_correct_version_metadata(self):
        original = renderer._alpha_world_return_distribution
        def shifted(inputs, **kwargs):
            sample, mean = original(inputs, **kwargs)
            return [v+.01 for v in sample], mean+.01
        self._reject('_alpha_world_return_distribution', shifted)


if __name__ == '__main__': unittest.main()
