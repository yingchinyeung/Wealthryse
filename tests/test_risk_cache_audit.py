import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts/audit_risk_cache.py"
spec = importlib.util.spec_from_file_location("risk_audit_test", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class RiskCacheAuditTests(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(audit.representative_dates([]), [])

    def test_short_history_deduplicated(self):
        self.assertEqual(audit.representative_dates(["2026-01-02"] * 3), ["2026-01-02"])
        self.assertEqual(audit.representative_dates(["2026-01-03", "2026-01-02"]), ["2026-01-02", "2026-01-03"])

    def test_first_middle_latest_sorted(self):
        self.assertEqual(audit.representative_dates(["2026-01-05", "2026-01-01", "2026-01-03", "2026-01-02", "2026-01-04"]),
                         ["2026-01-01", "2026-01-03", "2026-01-05"])

    def test_diagnostic_never_calls_rebuild_or_report_publish(self):
        source = PATH.read_text(encoding="utf-8")
        self.assertIn("REPEATABLE READ READ ONLY", source)
        self.assertIn("default_transaction_read_only=on", source)
        self.assertIn("lifecycle_status='active'", source)
        self.assertIn('globals().get("ALLOW_SAMPLE_RECALCULATION", False)', source)
        for forbidden in ("build_risk_snapshot(", "store_risk_snapshot(", "refresh_crisis_replay(", "initialize("):
            self.assertNotIn(forbidden, source)

    def test_output_comparison_ignores_build_time_not_financial_changes(self):
        before = {"metrics":{"volatility":0.2}, "crisis_replay":{"generated_at":"old","maximum_drawdown":-0.4}}
        after = {"metrics":{"volatility":0.2}, "crisis_replay":{"generated_at":"new","maximum_drawdown":-0.4}}
        self.assertEqual(audit.changed_result_sections(before, after), [])
        after["metrics"]["volatility"] = 0.21
        self.assertEqual(audit.changed_result_sections(before, after), ["metrics"])


if __name__ == "__main__":
    unittest.main()
