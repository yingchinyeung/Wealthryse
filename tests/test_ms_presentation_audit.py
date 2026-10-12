import copy
import unittest
from xml.etree import ElementTree as ET

from audit_alpha_ms_presentation import A, C, verify_chart, verify_ms_contribution_heading
from validate_monthly_presentation import _required_page_titles, validate
from pathlib import Path


class MsExportedChartAuditTests(unittest.TestCase):
    def test_required_page_title_change_is_scoped_to_ms_hkd(self):
        self.assertIn('Sector Drivers of Relative Performance', _required_page_titles())
        self.assertNotIn('Component Drivers of Monthly Return', _required_page_titles())
        self.assertIn('Component Drivers of Monthly Return', _required_page_titles(ms_hkd=True))
        self.assertNotIn('Sector Drivers of Relative Performance', _required_page_titles(ms_hkd=True))
        with self.assertRaisesRegex(RuntimeError,'Alpha World MS identity'):
            validate(Path('not-opened.pptx'), expected_period='2026-09-30',
                     expected_strategy='iStrategy: Other Account', ms_hkd=True)

    def test_ms_contribution_heading_must_not_claim_relative_performance(self):
        verify_ms_contribution_heading('Component Drivers of Monthly Return')
        for text in ('Sector Drivers of Relative Performance', 'Monthly review',
                     'Component Drivers of Monthly Return Sector Drivers of Relative Performance'):
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'absolute monthly return'):
                verify_ms_contribution_heading(text)

    def setUp(self):
        self.chart=ET.fromstring(f'''<c:chart xmlns:c="{C}" xmlns:a="{A}">
          <c:ser><c:cat><c:strLit><c:pt><c:v>Component</c:v></c:pt></c:strLit></c:cat>
          <c:val><c:numLit><c:pt><c:v>0.03</c:v></c:pt></c:numLit></c:val>
          <c:dLbls><c:dLbl><c:tx><a:t>-3.00%</a:t></c:tx></c:dLbl></c:dLbls></c:ser>
          <c:valAx><c:numFmt formatCode="0.00%"/>
          <c:title><a:t>Magnitude (signed labels)</a:t></c:title></c:valAx></c:chart>''')

    def check(self,chart):
        verify_chart(chart,['Component'],[.03],'0.00%',['-3.00%'])

    def test_correct_native_values_labels_and_axis_pass(self):
        before=ET.tostring(self.chart)
        self.check(self.chart)
        self.assertEqual(before,ET.tostring(self.chart))

    def test_changed_values_categories_units_or_signs_fail_closed(self):
        for tag,value,message in (
            ('cat//','Foreign account','categories'),
            ('val//','0.04','values'),
        ):
            chart=copy.deepcopy(self.chart)
            chart.find('.//{'+C+'}'+tag+'{'+C+'}v').text=value
            with self.subTest(tag=tag),self.assertRaisesRegex(ValueError,message):self.check(chart)
        for value in ('General','0'):
            chart=copy.deepcopy(self.chart)
            chart.find('.//{'+C+'}numFmt').set('formatCode',value)
            with self.subTest(unit=value),self.assertRaisesRegex(ValueError,'unit'):self.check(chart)
        chart=copy.deepcopy(self.chart)
        chart.find('.//{'+C+'}dLbl//{'+A+'}t').text='3.00%'
        with self.assertRaisesRegex(ValueError,'source signs'):self.check(chart)

    def test_missing_axis_format_or_magnitude_disclosure_rejected(self):
        chart=copy.deepcopy(self.chart);axis=chart.find('.//{'+C+'}valAx')
        axis.remove(axis.find('{'+C+'}numFmt'))
        with self.assertRaisesRegex(ValueError,'unit'):self.check(chart)
        chart=copy.deepcopy(self.chart)
        chart.find('.//{'+C+'}valAx//{'+A+'}t').text='Return'
        with self.assertRaisesRegex(ValueError,'ambiguous'):self.check(chart)

    def test_extra_benchmark_series_rejected(self):
        self.chart.append(copy.deepcopy(self.chart.find('{'+C+'}ser')))
        with self.assertRaisesRegex(ValueError,'series count'):self.check(self.chart)

    def test_single_observation_without_visible_marker_fails(self):
        with self.assertRaisesRegex(ValueError,'visible chart marker'):
            verify_chart(self.chart,['Component'],[.03],'0.00%',require_visible_marker=True)
        marker=ET.SubElement(self.chart.find('{'+C+'}ser'),'{'+C+'}marker')
        symbol=ET.SubElement(marker,'{'+C+'}symbol',{'val':'none'})
        size=ET.SubElement(marker,'{'+C+'}size',{'val':'7'})
        with self.assertRaisesRegex(ValueError,'visible chart marker'):
            verify_chart(self.chart,['Component'],[.03],'0.00%',require_visible_marker=True)
        symbol.set('val','circle');size.set('val','0')
        with self.assertRaisesRegex(ValueError,'visible chart marker'):
            verify_chart(self.chart,['Component'],[.03],'0.00%',require_visible_marker=True)
        size.set('val','7')
        verify_chart(self.chart,['Component'],[.03],'0.00%',require_visible_marker=True)
