from __future__ import annotations

import copy
import io
import math
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from pypdf.generic import ContentStream
from reportlab.pdfgen import canvas

from ibkr_etl.monthly_artifacts import (
    ALPHA_WORLD_FACTSHEET_LAYOUT,
    ALPHA_WORLD_MS_CHART_STYLE,
    _alpha_world_ms_line_axis,
    _alpha_world_ms_density_axis,
    _alpha_world_ms_distribution_ticks,
    _alpha_world_ms_skew_fit,
    _collect_text_runs,
    _draw_alpha_world_distribution,
    _draw_alpha_world_page_one,
    _draw_alpha_world_line_chart,
    factsheet_inputs,
    render_factsheet,
    MonthlyArtifactError,
)
from test_monthly_artifacts import _snapshot, _ms_snapshot, _write_alpha_world_source


def _overlay(snapshot):
    stream = io.BytesIO()
    c = canvas.Canvas(stream, pagesize=(595.276, 841.89))
    _draw_alpha_world_page_one(c, snapshot, factsheet_inputs(snapshot), 595.276, 841.89)
    c.save()
    return PdfReader(io.BytesIO(stream.getvalue())).pages[0]


class AlphaWorldMSChartStyleTests(unittest.TestCase):
    def test_distribution_quarter_ticks_keep_full_range_and_clean_zero(self):
        ticks, step = _alpha_world_ms_distribution_ticks(-.5, 1., .5)
        self.assertEqual(step, .25)
        self.assertEqual(ticks, [-.5,-.25,0.,.25,.5,.75,1.])
        self.assertEqual(_alpha_world_ms_distribution_ticks(-.4,.8,.2)[0],
                         [-.25,0.,.25,.5,.75])
        self.assertEqual(_alpha_world_ms_distribution_ticks(-.5000000000000001,1.5,.5)[0][-1],1.5)

    def test_near_flat_distribution_keeps_informative_fine_scale(self):
        ticks, step = _alpha_world_ms_distribution_ticks(0.,.01,.002)
        self.assertEqual(step,.002)
        self.assertEqual(ticks,[i*.002 for i in range(6)])

    def test_legacy_chart_with_current_metadata_is_rejected_before_delivery(self):
        snapshot = _ms_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.pdf'
            output = Path(directory) / 'output.pdf'
            _write_alpha_world_source(model)
            model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
            def legacy_rebased(c, rows, **kwargs):
                if kwargs['baseline_label'] == 'Base = 100':
                    kwargs['authority_style'] = False
                return _draw_alpha_world_line_chart(c, rows, **kwargs)
            with patch('ibkr_etl.monthly_artifacts.ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256', model_hash), patch('ibkr_etl.monthly_artifacts._draw_alpha_world_line_chart', side_effect=legacy_rebased):
                with self.assertRaisesRegex(MonthlyArtifactError, 'layout contract|geometry differs'):
                    render_factsheet(snapshot, template_path=model, design_model_path=model, output_path=output)
            self.assertFalse(output.exists())

    def test_density_axis_uses_half_unit_ticks_and_unclipped_peak(self):
        maximum, step = _alpha_world_ms_density_axis(3.12)
        self.assertAlmostEqual(maximum, 3.276)
        self.assertEqual(step, 0.5)

    def test_skew_curve_preserves_sample_mean_and_variance_and_correct_direction(self):
        sample = [(i / 100) ** 2 for i in range(101)]
        mean = sum(sample) / len(sample)
        variance = sum((v - mean) ** 2 for v in sample) / (len(sample) - 1)
        location, scale, shape = _alpha_world_ms_skew_fit(sample)
        delta = shape / math.sqrt(1 + shape * shape)
        self.assertGreater(shape, 0)
        self.assertLess(location, mean)
        self.assertAlmostEqual(location + scale * delta * math.sqrt(2 / math.pi), mean)
        self.assertAlmostEqual(scale * scale * (1 - 2 * delta * delta / math.pi), variance)
        def density(value):
            z = (value - location) / scale
            return math.exp(-z*z/2) * (1 + math.erf(shape*z/math.sqrt(2))) / (scale*math.sqrt(2*math.pi))
        mode = max((i / 1000 for i in range(1001)), key=density)
        self.assertLess(mode, mean)

    def test_axis_has_authority_padding_without_rounding_plot_bounds_to_ticks(self):
        minimum, maximum, step = _alpha_world_ms_line_axis([92.8, 100.0, 143.5])
        self.assertAlmostEqual(minimum, 90.265)
        self.assertAlmostEqual(maximum, 146.035)
        self.assertEqual(step, 10.0)
        self.assertEqual(list(range(math.ceil(minimum / step) * 10, int(maximum), 10)), [100, 110, 120, 130, 140])
        low, high, step = _alpha_world_ms_line_axis([100, 100])
        self.assertLess(low, 100)
        self.assertGreater(high, 100)
        self.assertGreater(step, 0)

    def test_ms_capital_is_a_100000_unit_illustration_without_mutating_nav(self):
        snapshot = _ms_snapshot()
        # Actual composite NAV starts at 100, not an account cash balance.
        for row in snapshot['performance']['series']:
            row['nav'] /= 1000
        before = copy.deepcopy(snapshot)
        page = _overlay(snapshot)
        text = page.extract_text()
        self.assertIn('Initial HKD 100,000', text)
        self.assertIn('HKD 100,000', text)
        self.assertIn('Base = 100', text)
        self.assertEqual(snapshot, before)
        runs = _collect_text_runs(page)
        self.assertFalse(any(value == 'USD' and x < 100 and 350 < y < 480 for value, x, y, _, _ in runs))
        rectangles = [tuple(map(float, args)) for args, op in ContentStream(page.get_contents(), page.pdf).operations if op == b're']
        self.assertIn((339.0, 453.5, 50.0, 7.5), rectangles)

    def test_t_capital_contract_is_not_silently_changed(self):
        snapshot = copy.deepcopy(_snapshot())
        snapshot['subject_id'] = 'alpha-world-t'
        for row in snapshot['performance']['series']:
            row['nav'] /= 1000
        text = _overlay(snapshot).extract_text()
        self.assertIn('Initial USD 100', text)
        self.assertNotIn('Initial USD 100,000', text)

    def test_masks_cover_legacy_images_but_do_not_clip_section_headings(self):
        layout = ALPHA_WORLD_FACTSHEET_LAYOUT['page_1']
        self.assertGreater(layout['period_box'][0], 202.668434912)
        x, y, width, height = layout['distribution_chart_clear']
        self.assertLessEqual(x, 82.205)
        self.assertLessEqual(y, 74.699)
        self.assertGreaterEqual(x + width, 541.428488)
        self.assertGreaterEqual(y + height, 268.539411648)
        self.assertLess(y + height, 288.7)  # section heading baseline
        self.assertLessEqual(layout['capital_chart_clear'][1], 323.7644328)

    def test_distribution_uses_reference_colors_zero_guide_ticks_and_legend(self):
        snapshot = _ms_snapshot()
        inputs = factsheet_inputs(snapshot)
        stream = io.BytesIO()
        c = canvas.Canvas(stream)
        # Fixed visualization sample, including negative/positive paths. Never
        # a source of reported account metrics or a change to MC sampling.
        sample = [-0.2 + i / 100 for i in range(100)] * 10
        mean = sum(sample) / len(sample)
        with patch('ibkr_etl.monthly_artifacts._alpha_world_return_distribution', return_value=(sample, mean)):
            _draw_alpha_world_distribution(c, inputs, {'annualized_return': 0.17}, box=(80, 90, 463, 153))
        c.save()
        page = PdfReader(io.BytesIO(stream.getvalue())).pages[0]
        text = page.extract_text()
        for label in ('1000 MC paths', 'Skew-normal fit', 'Normal fit', f'MC Mean: {mean:.1%}', 'Historical: 17.0%', 'Density', 'Annualized Return'):
            self.assertIn(label, text)
        ops = ContentStream(page.get_contents(), page.pdf).operations
        colors = [tuple(round(float(v), 6) for v in args) for args, op in ops if op in (b'rg', b'RG')]
        for key in ('bar_color', 'normal_color', 'mean_color', 'historical_color'):
            color = ALPHA_WORLD_MS_CHART_STYLE[key].lstrip('#')
            expected = tuple(round(int(color[i:i+2], 16) / 255, 6) for i in (0, 2, 4))
            self.assertTrue(any(all(abs(a-b) < 1e-5 for a,b in zip(actual, expected)) for actual in colors), key)
        # The zero-return guide has its own dotted grey stroke and line width.
        self.assertIn(([0.4], b'w'), [(list(map(float, args)), op) for args, op in ops if op == b'w'])


if __name__ == '__main__':
    unittest.main()
