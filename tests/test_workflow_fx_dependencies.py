import copy
import unittest
from unittest.mock import patch
from test_workflow_financial_inputs import fixture, ASOF
from ibkr_etl.workflow_fx_dependencies import trace_fx_dependencies


class FxDependencyTests(unittest.TestCase):
    def trace(self, bundle): return trace_fx_dependencies(bundle, base_currency="USD")

    def foreign(self, field="currency", table="position_snapshots"):
        bundle = fixture();bundle["tables"][table][0][field] = "HKD"
        bundle["tables"]["fx_rates"][0].update(from_currency="HKD", to_currency="USD", rate_text="0.13")
        return bundle

    def test_prior_quote_without_forward_look_and_no_mutation(self):
        bundle = self.foreign();bundle["tables"]["fx_rates"][0]["rate_date"] = "2025-12-01"
        before = copy.deepcopy(bundle);result = self.trace(bundle)
        self.assertTrue(result["conservative_dependency_coverage_verified"])
        self.assertEqual(result["selection_counts"], {"direct": 1})
        self.assertEqual(bundle, before)
        self.assertFalse(result["financial_accuracy_verified"])
        self.assertFalse(result["actual_financial_consumption_verified"])

    def test_all_currency_fields_and_trade_pair_legs_included(self):
        for table, field in [("cash_snapshots", "currency"), ("nav_snapshots", "currency"),
                             ("cash_transactions", "currency"), ("trades", "commission_currency"),
                             ("corporate_actions", "currency"), ("fx_transactions", "fx_currency"),
                             ("fx_transactions", "functional_currency")]:
            with self.subTest(table=table, field=field):
                self.assertEqual(self.trace(self.foreign(field, table))["dated_demands"], 1)
        bundle = fixture();bundle["instruments"][0].update(asset_category="CASH", symbol="EUR.USD")
        self.assertIn("EUR:missing_observation", self.trace(bundle)["failure_counts"])

    def test_invalid_direct_cannot_expose_prior_or_inverse_rate(self):
        bundle = self.foreign();bad = bundle["tables"]["fx_rates"][0]
        old = dict(bad, rate_date="2026-10-01", rate_text="0.13")
        inverse = dict(bad, from_currency="USD", to_currency="HKD", rate_text="7.7")
        bad["rate_text"] = "0";bundle["tables"]["fx_rates"] += [old, inverse]
        self.assertEqual(self.trace(bundle)["failure_counts"], {"HKD:invalid_latest_observation": 1})

    def test_valid_reciprocal_only_when_no_direct_quote(self):
        bundle = self.foreign();bundle["tables"]["fx_rates"][0].update(from_currency="USD", to_currency="HKD", rate_text="7.7")
        self.assertEqual(self.trace(bundle)["selection_counts"], {"reciprocal": 1})

    def test_base_cash_summary_is_counted_not_converted_or_mutated(self):
        bundle = fixture(); bundle["tables"]["cash_snapshots"][0]["currency"] = "BASE_SUMMARY"
        before = copy.deepcopy(bundle); result = self.trace(bundle)
        self.assertEqual(result["base_cash_summary_rows"], 1)
        self.assertEqual(result["dated_demands"], 0)
        self.assertTrue(result["conservative_dependency_coverage_verified"])
        self.assertEqual(bundle, before)
        # A marker in an actual transaction must not silently gain this rule.
        bundle["tables"]["trades"][0]["currency"] = "BASE_SUMMARY"
        self.assertEqual(self.trace(bundle)["failure_counts"], {"BASE_SUMMARY:missing_observation": 1})

    def test_missing_and_undated_dependencies_fail_closed(self):
        bundle = self.foreign();bundle["tables"]["fx_rates"] = []
        self.assertFalse(self.trace(bundle)["conservative_dependency_coverage_verified"])
        bundle = self.foreign("currency", "cash_transactions")
        bundle["tables"]["cash_transactions"][0]["effective_date"] = None
        result = self.trace(bundle)
        self.assertFalse(result["conservative_dependency_coverage_verified"])
        self.assertEqual(result["undated_counts"], {"cash_transactions:currency": 1})

    def test_unused_invalid_pair_remains_visible_without_claiming_financial_validity(self):
        bundle = fixture();bundle["tables"]["fx_rates"][0].update(from_currency="RUS", to_currency="USD", rate_text="-1")
        result = self.trace(bundle)
        self.assertFalse(result["invalid_stored_pairs"][0]["appears_in_dated_demands"])
        self.assertTrue(result["conservative_dependency_coverage_verified"])
        self.assertFalse(result["financial_accuracy_verified"])

    def test_same_count_quote_changes_dependency_fingerprint(self):
        bundle = self.foreign();before = self.trace(bundle)
        bundle["tables"]["fx_rates"][0]["rate_text"] = "0.14"
        self.assertNotEqual(self.trace(bundle)["dependency_sha256"], before["dependency_sha256"])

    def test_scope_and_demand_limits_enforced(self):
        bundle = self.foreign();bundle["tables"]["position_snapshots"][0]["account_id"] = "other"
        with self.assertRaises(ValueError): self.trace(bundle)
        with patch("ibkr_etl.workflow_fx_dependencies.MAX_DEMANDS", 0), self.assertRaises(ValueError):
            self.trace(self.foreign())


if __name__ == "__main__": unittest.main()
