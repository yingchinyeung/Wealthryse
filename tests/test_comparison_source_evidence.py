import copy
import unittest
from datetime import date
from unittest.mock import Mock
from ibkr_etl.comparison_source_evidence import verify_sources, load_source_projection, PROVIDER, STATUS


class ComparisonSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.contract = dict(tenant_id="tenant", subject_id="account", benchmark_id="index",
            history_start_date="2026-09-29", base_currency="USD", reporting_currency="USD",
            mapping_currency="USD", benchmark_currency="HKD")
        self.data = {"contract": self.contract, "as_of": "2026-09-30",
            "nav": [{"snapshot_date": "2026-09-29", "total_text": "100"},
                    {"snapshot_date": "2026-09-30", "total_text": "102"}],
            "prices": [dict(observation_date=day, level=level, currency="HKD", provider=PROVIDER,
                observation_status=STATUS, quality_status="warn") for day, level in
                [("2026-09-29", 800), ("2026-09-30", 880)]],
            "fx": [dict(rate_date="2026-09-29", rate=.125, from_currency="HKD", to_currency="USD",
                        provider=PROVIDER, observation_status=STATUS, quality_status="warn")],
            "comparisons": []}
        for day, value, level, growth, pr, br, er in [
            ("2026-09-29", 100, 100, 100, None, None, None),
            ("2026-09-30", 102, 110, 102, .02, .1, -.08)]:
            self.data["comparisons"].append(dict(tenant_id="tenant", subject_id="account", benchmark_id="index",
                observation_date=day, reporting_currency="USD", portfolio_value=value, benchmark_level=level,
                normalized_benchmark=level, normalized_portfolio=growth, portfolio_return=pr,
                benchmark_return=br, excess_return=er, calculation_version="benchmark-comparison-v1", quality_status="warn"))

    def verify(self):
        return verify_sources(self.data, expected_date=date(2026, 9, 30))

    def test_independent_fx_and_benchmark_checks_pass_without_workflow_authority(self):
        result = self.verify()
        self.assertTrue(result["source_projection_verified"])
        for key in ("cash_flows_verified", "ingestion_verified", "execution_authorized"):
            self.assertFalse(result[key])

    def test_corrected_nav_same_rows_invalidates_projection_and_fingerprint(self):
        original = self.verify()
        self.data["nav"][1]["total_text"] = "103"
        result = self.verify()
        self.assertNotEqual(original["source_sha256"], result["source_sha256"])
        self.assertIn("portfolio_value_mismatch", result["issue_counts"])

    def test_corrected_fx_and_price_invalidate(self):
        for field in ("fx", "prices"):
            changed = copy.deepcopy(self.data)
            changed[field][-1]["rate" if field == "fx" else "level"] *= 1.01
            result = verify_sources(changed, expected_date=date(2026, 9, 30))
            self.assertIn("benchmark_level_mismatch", result["issue_counts"])

    def test_missing_stale_and_future_fx_rejected(self):
        for day in ("2026-09-20", "2026-10-01"):
            self.data["fx"][0]["rate_date"] = day
            self.assertIn("missing_or_stale_prior_fx", self.verify()["issue_counts"])
        self.data["fx"] = []
        self.assertFalse(self.verify()["source_projection_verified"])

    def test_duplicate_sources_are_not_silently_deduplicated(self):
        for key in ("nav", "prices", "fx", "comparisons"):
            changed = copy.deepcopy(self.data)
            changed[key].append(changed[key][0])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "duplicate"):
                verify_sources(changed, expected_date=date(2026, 9, 30))

    def test_wrong_account_currency_or_method_rejected(self):
        for key in ("tenant_id", "subject_id", "benchmark_id", "reporting_currency", "calculation_version"):
            changed = copy.deepcopy(self.data)
            changed["comparisons"][0][key] = "wrong"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity"):
                verify_sources(changed, expected_date=date(2026, 9, 30))

    def test_failed_or_wrong_source_not_trusted(self):
        for key, value in [("quality_status", "fail"), ("provider", "unknown"), ("currency", "USD"),
                           ("observation_status", "intraday")]:
            changed = copy.deepcopy(self.data)
            changed["prices"][0][key] = value
            with self.assertRaisesRegex(ValueError, "source"):
                verify_sources(changed, expected_date=date(2026, 9, 30))

    def test_date_inventory_and_freshness_fail_closed(self):
        self.data["comparisons"].pop()
        self.assertIn("comparison_date_inventory_mismatch", self.verify()["issue_counts"])
        self.data["prices"].pop()
        self.assertIn("source_coverage_mismatch", self.verify()["issue_counts"])

    def test_every_calculated_field_checked(self):
        for key in ("portfolio_value", "benchmark_level", "normalized_portfolio", "normalized_benchmark",
                    "benchmark_return", "excess_return"):
            changed = copy.deepcopy(self.data)
            changed["comparisons"][1][key] += .01
            result = verify_sources(changed, expected_date=date(2026, 9, 30))
            self.assertIn(key + "_mismatch", result["issue_counts"])

    def test_nonfinite_and_nonpositive_source_values_rejected(self):
        for value in ("NaN", "Infinity", "0", "-1"):
            self.data["nav"][0]["total_text"] = value
            with self.assertRaises(ValueError):
                self.verify()

    def test_usd_benchmark_needs_no_fx(self):
        self.contract["benchmark_currency"] = "USD"
        self.data["fx"] = []
        for row in self.data["prices"]:
            row["currency"] = "USD"
            row["level"] *= .125
        self.assertTrue(self.verify()["source_projection_verified"])

    def test_loader_rejects_mutable_or_wrong_tenant_transaction(self):
        for values in [("on", "tenant-b", "repeatable read"), ("off", "tenant", "repeatable read"),
                       ("on", "tenant", "read committed")]:
            connection = Mock()
            connection.execute.return_value.fetchone.return_value = dict(zip(("read_only", "tenant_id", "isolation"), values))
            with self.assertRaisesRegex(ValueError, "READ ONLY"):
                load_source_projection(connection, tenant_id="tenant", subject_id="account", benchmark_id="index", as_of="2026-09-30")
            self.assertEqual(connection.execute.call_count, 1)
