"""Metrics guide: coverage of every metric the website shows, and the page's navigation aids."""
from __future__ import annotations

import re
import unittest
from html import unescape
from pathlib import Path

from ibkr_etl import metrics_guide as guide

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/ibkr_etl"
WEB_SOURCES = (SRC / "web.py", ROOT / "deploy/live-merged/src/ibkr_etl/web.py")


def _metric_card_labels(source: str, start: str, end: str) -> set[str]:
    """First element of each ``['Label', value]`` entry between two markers."""

    labels: set[str] = set()
    position = 0
    while (begin := source.find(start, position)) != -1:
        finish = source.find(end, begin)
        labels.update(re.findall(r"\['([^']+)',", source[begin:finish]))
        position = finish
    return labels


class MetricsGuideCoverageTest(unittest.TestCase):
    def test_every_metric_card_on_the_website_has_a_definition(self):
        labels = _metric_card_labels(
            (SRC / "web_analytics_ui.py").read_text(encoding="utf-8"),
            "analytics-statistics').innerHTML=[",
            "].map(metricCard)",
        )
        for path in WEB_SOURCES:
            source = path.read_text(encoding="utf-8")
            labels |= _metric_card_labels(source, "riskItems=[[", "]];")
            labels |= _metric_card_labels(source, "items=[['Portfolio total return'", "]];")
            labels |= _metric_card_labels(source, "msItems.push(", ");")
        self.assertGreater(len(labels), 30)
        self.assertEqual(sorted(labels - set(guide.METRIC_ANCHORS)), [])

    def test_overview_tiles_link_to_existing_definitions(self):
        source = (SRC / "web_ui.py").read_text(encoding="utf-8")
        anchors = set(re.findall(r"'(metric-[a-z0-9-]+)'\)", source))
        self.assertTrue({"metric-time-weighted-return-twr", "metric-irr-annualised",
                         "metric-net-external-flows"} <= anchors)
        self.assertEqual(sorted(anchors - set(guide.METRIC_ANCHORS.values())), [])

    def test_figures_added_since_the_analytics_upgrade_are_defined(self):
        for name in ("Day change", "Time-weighted return (TWR)", "TWR annualised", "IRR annualised",
                     "Net external flows", "Data freshness", "Benchmark return basis",
                     "Drawdown episode", "Value in reporting currency", "Cost basis",
                     "Unrealized P&L", "P&L %", "Holding weight", "Sector allocation"):
            self.assertIn(name, guide.METRIC_ANCHORS)
            self.assertIn(name, guide.CLIENT_CALCULATIONS)

    def test_definitions_do_not_hard_code_one_account(self):
        for section in guide.METRIC_SECTIONS:
            self.assertNotIn("U16088571", section.introduction)
            for metric in section.metrics:
                text = " ".join((metric.meaning, metric.calculation, metric.interpretation,
                                 guide.CLIENT_CALCULATIONS[metric.name]))
                self.assertNotIn("U16088571", text, metric.name)
                self.assertNotIn("20 January 2025", text, metric.name)

    def test_sections_follow_the_website_tabs_and_place_each_metric_once(self):
        keys = [section.key for section in guide.METRIC_SECTIONS]
        self.assertEqual(keys, ["overview", "benchmark", "statistics", "holdings", "risk",
                                "cash-trades", "reports", "system"])
        names = [metric.name for section in guide.METRIC_SECTIONS for metric in section.metrics]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(guide.CLIENT_CALCULATIONS))


class MetricsGuidePageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = guide.render_metrics_guide()

    def test_jump_bar_links_every_section_with_its_count(self):
        for section in guide.METRIC_SECTIONS:
            self.assertIn(f'id="guide-{section.key}"', self.page)
            self.assertIn(
                f'href="#guide-{section.key}" data-section-chip="guide-{section.key}">'
                f"{section.short_title} <span>{len(section.metrics)}</span>",
                unescape(self.page),
            )
        self.assertEqual(self.page.count('class="metric-back"'), len(guide.METRIC_SECTIONS))

    def test_aliases_are_searchable_and_related_links_resolve(self):
        etl = re.search(r'id="metric-daily-expected-tail-loss-95"[^>]*data-metric-search="([^"]*)"', self.page)
        self.assertIsNotNone(etl)
        self.assertIn("cvar", etl.group(1))
        self.assertIn("expected shortfall", etl.group(1))
        anchors = set(guide.METRIC_ANCHORS.values())
        related = re.findall(r'<div class="metric-related">(.*?)</div>', self.page)
        self.assertGreater(len(related), 40)
        for block in related:
            for anchor in re.findall(r'href="#([^"]+)"', block):
                self.assertIn(anchor, anchors)

    def test_each_definition_has_a_copy_link_button(self):
        self.assertEqual(self.page.count('class="metric-copy"'), len(guide.METRIC_ANCHORS))
        self.assertIn(f'data-anchor="{guide.METRIC_ANCHORS["Sharpe ratio"]}"', self.page)

    def test_conventions_panel_lists_methodology_versions(self):
        self.assertIn("Calculation conventions used throughout", self.page)
        for version, _ in guide.METHODOLOGY_VERSIONS:
            self.assertIn(f"<code>{version}</code>", self.page)

    def test_script_supports_prefill_expand_print_and_copy(self):
        script = guide.METRICS_GUIDE_SCRIPT
        self.assertIn("new URLSearchParams(location.search).get('q')", script)
        self.assertIn("'beforeprint'", script)
        self.assertIn("#metric-expand", script)
        self.assertIn("navigator.clipboard", script)
        self.assertIn('id="metric-expand"', self.page)


if __name__ == "__main__":
    unittest.main()
