import copy
import unittest
from decimal import Decimal
from unittest.mock import Mock
from ibkr_etl.cashflow_source_evidence import reconstruct_flows, verify_cashflow_returns, load_cashflow_sources


class CashflowSourceTests(unittest.TestCase):
    def setUp(self):
        self.data = {"contract": dict(tenant_id="t", subject_id="a", subject_type="physical_account",
            currency="USD", base_currency="USD", start="2026-09-01", as_of="2026-09-30"),
            "transfers": [dict(account_id="a", record_type="Transfer", source_key="one", ingestion_status="success",
                ingestion_completed_at="2026-09-30", values=dict(date="20260930", assetCategory="CASH", currency="USD", cashTransfer="20"))],
            "fx": [], "ledger": []}
        self.rows = [dict(tenant_id="t", subject_id="a", benchmark_id="b", reporting_currency="USD",
            calculation_version="benchmark-comparison-v1", quality_status="warn", observation_date=day,
            portfolio_value=value, portfolio_return=ret) for day, value, ret in
            [("2026-09-29", 100, None), ("2026-09-30", 131, .1)]]

    def verify(self):
        return verify_cashflow_returns(self.data, self.rows)

    def test_deposit_adjusted_return_and_no_authority(self):
        result = self.verify()
        self.assertTrue(result["implemented_flow_policy_verified"])
        self.assertFalse(result["ingestion_completeness_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertNotIn("values", result)

    def test_changed_flow_same_count_invalidates_return(self):
        original = self.verify()
        self.data["transfers"][0]["values"]["cashTransfer"] = "21"
        result = self.verify()
        self.assertNotEqual(result["source_sha256"], original["source_sha256"])
        self.assertEqual(result["mismatch_count"], 1)

    def test_withdrawals_and_multiple_flows_sum(self):
        second = copy.deepcopy(self.data["transfers"][0])
        second["source_key"] = "two"
        second["values"]["cashTransfer"] = "-10"
        self.data["transfers"].append(second)
        self.rows[1]["portfolio_value"] = 120.5
        self.assertTrue(self.verify()["implemented_flow_policy_verified"])

    def test_no_future_fx_and_missing_amount_fails(self):
        self.data["transfers"][0]["values"]["currency"] = "HKD"
        self.data["fx"] = [dict(account_id="a", rate_date="2026-10-01", from_currency="HKD", to_currency="USD", rate_text=".125")]
        self.assertIn("transfer_fx_missing", self.verify()["issues"])
        self.data["transfers"][0]["values"]["cashTransfer"] = ""
        self.assertIn("invalid_transfer_amount", self.verify()["issues"])

    def test_stale_fx_warning_retained(self):
        self.data["transfers"][0]["values"].update(currency="HKD", cashTransfer="160")
        self.data["fx"] = [dict(account_id="a", rate_date="2026-09-20", from_currency="HKD", to_currency="USD", rate_text=".125")]
        result = self.verify()
        self.assertTrue(result["implemented_flow_policy_verified"])
        self.assertIn("transfer_fx_older_than_four_days", result["warnings"])

    def test_invalid_unused_fx_is_visible_but_not_mistaken_for_consumed_fx(self):
        self.data["fx"] = [dict(account_id="a", rate_date="2026-09-30", from_currency="HKD", to_currency="USD", rate_text="0")]
        result = self.verify()
        self.assertTrue(result["implemented_flow_policy_verified"])
        self.assertIn("unused_invalid_fx_observations:1", result["warnings"])
        self.data["transfers"][0]["values"]["currency"] = "HKD"
        result = self.verify()
        self.assertFalse(result["implemented_flow_policy_verified"])
        self.assertIn("transfer_fx_invalid", result["issues"])

    def test_security_transfer_uses_explicit_base_amount(self):
        self.data["transfers"][0]["values"] = dict(date="2026-09-30", assetCategory="STK", positionAmountInBase="20")
        self.assertTrue(self.verify()["implemented_flow_policy_verified"])

    def test_malformed_date_and_failed_lineage_never_skip(self):
        self.data["transfers"][0]["values"]["date"] = "20261340"
        self.assertIn("invalid_transfer_date", self.verify()["issues"])
        self.data["transfers"][0]["values"]["date"] = "20260930"
        self.data["transfers"][0]["ingestion_status"] = "failed"
        self.assertIn("transfer_ingestion_lineage_not_successful", self.verify()["issues"])

    def test_duplicate_and_wrong_account_rejected(self):
        self.data["transfers"].append(self.data["transfers"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.verify()
        self.data["transfers"].pop()
        self.data["transfers"][0]["account_id"] = "other"
        with self.assertRaisesRegex(ValueError, "scope"):
            self.verify()

    def test_derived_only_included_external_flows_count(self):
        self.data["contract"]["subject_type"] = "filtered_portfolio"
        base = dict(portfolio_id="a", event_key="one", event_date="2026-09-30", included=1,
            external_flow=1, amount=160, fx_rate_to_base=.125, amount_base=20)
        self.data["ledger"] = [base, dict(base, event_key="two", external_flow=0), dict(base, event_key="three", included=0)]
        self.assertTrue(self.verify()["implemented_flow_policy_verified"])
        self.data["ledger"][0]["amount_base"] = 21
        self.assertIn("derived_flow_conversion_mismatch", self.verify()["issues"])

    def test_period_boundary_opening_flow_not_counted_twice(self):
        self.data["transfers"][0]["values"]["date"] = "20260929"
        self.rows[1].update(portfolio_value=110, portfolio_return=.1)
        self.assertTrue(self.verify()["implemented_flow_policy_verified"])

    def test_currency_and_comparison_scope_enforced(self):
        self.rows[1]["benchmark_id"] = "other"
        with self.assertRaisesRegex(ValueError, "scope"):
            self.verify()
        self.rows[1]["benchmark_id"] = "b"
        self.data["contract"]["currency"] = "HKD"
        with self.assertRaisesRegex(ValueError, "currency"):
            self.verify()

    def test_nonpositive_denominator_and_nonfinite_return_fail(self):
        self.data["transfers"][0]["values"]["cashTransfer"] = "-200"
        self.assertIn("nonpositive_flow_adjusted_denominator", self.verify()["issues"])
        self.data["transfers"][0]["values"]["cashTransfer"] = "20"
        self.rows[1]["portfolio_return"] = "NaN"
        self.assertIn("invalid_saved_portfolio_return", self.verify()["issues"])

    def test_loader_rejects_nonreadonly_transaction(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = dict(tenant_id="t", ro="off", isolation="repeatable read")
        with self.assertRaisesRegex(ValueError, "READ ONLY"):
            load_cashflow_sources(connection, tenant_id="t", subject_id="a", as_of="2026-09-30")
        self.assertEqual(connection.execute.call_count, 1)
