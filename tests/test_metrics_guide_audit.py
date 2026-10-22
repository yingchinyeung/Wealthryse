"""Metrics guide audit: finds website figures with no definition and changed calculations."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ibkr_etl import metrics_guide as guide
from ibkr_etl import metrics_guide_audit as audit

ROOT = Path(__file__).resolve().parents[1]


class AuditOfTheRepositoryTest(unittest.TestCase):
    def test_the_guide_is_up_to_date_with_the_website(self):
        report = audit.audit()
        self.assertEqual(report.uncovered_labels, {})
        self.assertEqual(report.missing_sources, [])
        self.assertEqual(report.unrecorded_sources, [])

    def test_watch_list_and_label_map_name_real_definitions_and_labels(self):
        for source, metrics in audit.WATCHED_SOURCES.items():
            for name in metrics:
                self.assertIn(name, guide.METRIC_ANCHORS, source)
        for label, name in audit.LABEL_MAP.items():
            self.assertIn(name, guide.METRIC_ANCHORS, label)
        self.assertEqual(sorted(set(audit.LABEL_MAP) - set(audit.website_labels())), [])

    def test_labels_include_modelling_tiles_and_what_if_rows(self):
        labels = audit.website_labels()
        for label in ("Shock half-life", "Regime now", "Worst month (21 sessions)", "Sharpe ratio",
                      "Tracking error", "Calmar ratio"):
            self.assertIn(label, labels)
        self.assertEqual(audit.metric_for_label("Regime now"), "Market regime")
        self.assertEqual(audit.metric_for_label("cvar"), "Daily Expected Tail Loss (95%)")
        self.assertIsNone(audit.metric_for_label("Something brand new"))


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        files = {f"ibkr_etl/{name}" for name, *_ in audit.LABEL_SOURCES}
        files |= {source.split(":")[0].replace(".", "/") + ".py" for source in audit.WATCHED_SOURCES}
        for relative in files:
            (self.tmp / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / "src" / relative, self.tmp / relative)
        self.manifest = self.tmp / "manifest.json"
        audit.write_manifest(self.tmp, self.manifest)

    def _edit(self, relative: str, old: str, new: str) -> None:
        path = self.tmp / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_a_fresh_manifest_is_clean(self):
        self.assertTrue(audit.audit(self.tmp, self.manifest).clean)

    def test_comments_and_docstrings_do_not_count_as_changes(self):
        self._edit("ibkr_etl/risk_forecasting.py", "def var_backtest(",
                   "# A comment about the backtest\ndef var_backtest(")
        self._edit("ibkr_etl/risk_forecasting.py", "each day's VaR uses only earlier returns",
                   "each day's VaR is fitted on earlier returns only")
        self.assertTrue(audit.audit(self.tmp, self.manifest).clean)

    def test_a_changed_calculation_names_the_definitions_to_reread(self):
        self._edit("ibkr_etl/risk_forecasting.py", "def var_backtest(returns: Sequence[float], dates: Sequence[str], *, level: float = 0.95,",
                   "def var_backtest(returns: Sequence[float], dates: Sequence[str], *, level: float = 0.99,")
        report = audit.audit(self.tmp, self.manifest)
        self.assertEqual(report.changed_sources, {"ibkr_etl.risk_forecasting:var_backtest": ("VaR backtest",)})
        brief = audit.brief(report, self.tmp)
        self.assertIn("level: float = 0.99", brief)
        self.assertIn('Current definition of "VaR backtest"', brief)
        self.assertIn("--refresh", brief)

    def test_a_changed_module_constant_counts_as_a_change(self):
        self._edit("ibkr_etl/modelling.py", "PROJECTION_PATHS = 2000", "PROJECTION_PATHS = 4000")
        report = audit.audit(self.tmp, self.manifest)
        self.assertIn("ibkr_etl.modelling:project_wealth", report.changed_sources)

    def test_a_new_tile_without_a_definition_is_reported(self):
        self._edit("ibkr_etl/web_modelling.py", "modelTile('Regime now'",
                   "modelTile('Regime stickiness',1),modelTile('Regime now'")
        report = audit.audit(self.tmp, self.manifest)
        self.assertEqual(report.uncovered_labels, {"Regime stickiness": "src/ibkr_etl/web_modelling.py"})
        self.assertIn("`Regime stickiness`", audit.brief(report, self.tmp))

    def test_a_renamed_function_is_reported(self):
        self._edit("ibkr_etl/modelling.py", "def risk_contributions(", "def risk_shares(")
        self.assertEqual(audit.audit(self.tmp, self.manifest).missing_sources,
                         ["ibkr_etl.modelling:risk_contributions"])


class CommandAndWorkflowTest(unittest.TestCase):
    def test_command_reports_clean_and_warn_mode_never_fails(self):
        for args, expected in (([], 0), (["--warn"], 0), (["--json"], 0)):
            result = subprocess.run([sys.executable, "scripts/check_metrics_guide.py", *args],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_fingerprints_leave_out_empty_fields_so_python_versions_agree(self):
        import ast

        text = audit._canonical(ast.parse("def f(x):\n    return x + 1\n").body[0])
        self.assertNotIn("None", text)
        self.assertNotIn("[]", text)
        self.assertNotIn("type_params", text)

    def test_ci_warns_and_the_manifest_ships_with_the_package(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/check_metrics_guide.py --warn", ci)
        self.assertIn("metrics_guide_sources.json", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertFalse((ROOT / ".github/workflows/metrics-guide-keeper.yml").exists())

    def test_drift_facts_carry_the_code_definitions_and_their_numbers(self):
        report = audit.AuditReport(changed_sources={"ibkr_etl.risk_forecasting:var_backtest": ("VaR backtest",)})
        facts = audit.drift_facts(report)
        item = facts["changed_calculations"][0]
        self.assertIn("def var_backtest(", item["code"])
        self.assertEqual(item["definitions"][0]["name"], "VaR backtest")
        self.assertIn(0.95, facts["figures_in_source"])
        self.assertIn(63.0, facts["figures_in_source"])
        self.assertEqual(len(audit.drift_key(report)), 16)
        self.assertNotEqual(audit.drift_key(report), audit.drift_key(audit.AuditReport()))


if __name__ == "__main__":
    unittest.main()
