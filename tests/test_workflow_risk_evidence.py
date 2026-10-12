import copy
import hashlib
import unittest
from datetime import date
from dataclasses import replace
from unittest.mock import Mock, patch

from ibkr_etl.benchmark_analytics import ComparisonRow, CALCULATION_VERSION
from ibkr_etl.risk_snapshots import RISK_ANALYTICS_VERSION, risk_input_fingerprint
from ibkr_etl.util import canonical_json
from ibkr_etl.workflow_risk_evidence import verify_output, semantic_hash, inspect_risk_output

TENANT = "00000000-0000-0000-0000-000000000001"


class RiskProducerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {"subject_id": "account-a", "subject_type": "physical_account",
            "as_of": "2026-09-30", "benchmark_id": "benchmark-a",
            "rows": [ComparisonRow("2026-09-30", 100., 100., 100., 100., .01, .02, -.01)],
            "positions": [], "risk_free": {"rate_date": "2026-09-30", "annual_rate": .04},
            "crisis_replay": {"holdings_as_of": "2026-09-30", "maximum_drawdown": -.4, "generated_at": "old"}}
        self.payload = {"account_id": "account-a", "as_of": "2026-09-30", "benchmark_id": "benchmark-a",
            "analytics_version": RISK_ANALYTICS_VERSION, "quality_status": "warn",
            "metrics": {"volatility": .1}, "crisis_replay": copy.deepcopy(self.inputs["crisis_replay"])}
        self.existing = {"tenant_id": TENANT, "subject_id": "account-a", "as_of_date": "2026-09-30",
            "analytics_version": RISK_ANALYTICS_VERSION, "comparison_version": CALCULATION_VERSION,
            "quality_status": "warn", "payload_json": copy.deepcopy(self.payload)}
        self.rehash()

    def rehash(self):
        self.existing["source_data_sha256"] = risk_input_fingerprint(self.inputs)
        self.existing["payload_sha256"] = hashlib.sha256(canonical_json(self.existing["payload_json"]).encode()).hexdigest()

    def verify(self, **overrides):
        args = dict(tenant_id=TENANT, subject_id="account-a", as_of="2026-09-30",
            expected_comparison_date=date(2026, 9, 30), inputs=self.inputs,
            existing=self.existing, recomputed=self.payload,
            recomputed_source_sha256=risk_input_fingerprint(self.inputs))
        args.update(overrides)
        return verify_output(**args)

    def test_matching_output_never_grants_prerequisite_or_execution_authority(self):
        result = self.verify()
        self.assertTrue(result["output_verified"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["prerequisites_verified"])
        self.assertIn("producer_quality_warn", result["warnings"])
        self.assertNotIn("metrics", result)
        self.assertNotIn("positions", result)

    def test_matching_hashes_do_not_hide_wrong_numbers(self):
        self.existing["payload_json"]["metrics"]["volatility"] = .2
        self.rehash()
        self.assertIn("deterministic_output_mismatch", self.verify()["reasons"])

    def test_corrected_input_same_date_and_counts_blocks(self):
        self.inputs["risk_free"]["annual_rate"] = .05
        self.assertIn("cache:inputs_changed", self.verify()["reasons"])

    def test_empty_or_duplicate_or_unordered_comparisons_block(self):
        row = self.inputs["rows"][0]
        for rows in ([], [row, row], [row, replace(row, observation_date="2026-09-29")]):
            self.inputs["rows"] = rows
            self.rehash()
            self.assertIn("comparison_dates_missing_duplicate_or_unordered", self.verify()["reasons"])

    def test_stale_comparisons_cannot_pass_using_payload_asof_label(self):
        self.inputs["rows"] = [replace(self.inputs["rows"][0], observation_date="2026-09-29")]
        self.rehash()
        self.assertIn("comparison_coverage_mismatch", self.verify()["reasons"])

    def test_future_comparison_and_rate_block(self):
        self.inputs["rows"] = [replace(self.inputs["rows"][0], observation_date="2026-10-01")]
        self.inputs["risk_free"]["rate_date"] = "2026-10-01"
        self.rehash()
        self.assertIn("comparison_coverage_mismatch", self.verify()["reasons"])
        self.assertIn("future_risk_free_observation", self.verify()["reasons"])

    def test_identity_bound_to_tenant_account_date_and_version(self):
        for key in ("tenant_id", "subject_id", "as_of_date", "analytics_version"):
            changed = dict(self.existing, **{key: "wrong"})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "scope mismatch"):
                self.verify(existing=changed)
        self.inputs["subject_id"] = "account-b"
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            self.verify()

    def test_explicit_expected_date_required(self):
        for expected in (None, "2026-09-30", date(2026, 10, 1)):
            with self.assertRaises(ValueError):
                self.verify(expected_comparison_date=expected)

    def test_missing_and_corrupt_cache_do_not_pass(self):
        self.assertIn("cache:missing", self.verify(existing=None)["reasons"])
        self.existing["payload_json"] = "not-json"
        self.assertIn("cache:invalid_payload", self.verify()["reasons"])

    def test_only_replay_clock_ignored(self):
        changed = copy.deepcopy(self.payload)
        changed["crisis_replay"]["generated_at"] = "new"
        self.assertEqual(semantic_hash(changed), semantic_hash(self.payload))
        changed["crisis_replay"]["maximum_drawdown"] = -.5
        self.assertNotEqual(semantic_hash(changed), semantic_hash(self.payload))
        changed["metrics"]["volatility"] = float("nan")
        with self.assertRaises(ValueError):
            semantic_hash(changed)

    def test_missing_metric_rate_and_crisis_not_silently_certified(self):
        self.inputs["risk_free"] = None
        self.inputs["crisis_replay"] = None
        self.payload["metrics"]["sharpe"] = None
        self.existing["payload_json"] = copy.deepcopy(self.payload)
        self.rehash()
        warnings = self.verify()["warnings"]
        self.assertIn("risk_free_unavailable", warnings)
        self.assertIn("crisis_replay_unavailable", warnings)
        self.assertIn("one_or_more_metrics_unavailable", warnings)

    def test_different_recomputed_input_and_identity_block(self):
        self.assertIn("recomputation_input_mismatch", self.verify(recomputed_source_sha256="0" * 64)["reasons"])
        changed = dict(self.payload, account_id="account-b")
        self.assertIn("recomputation_identity_mismatch", self.verify(recomputed=changed)["reasons"])

    def test_invalid_metrics_structure_and_failed_quality_block(self):
        changed = dict(self.payload, metrics=[], quality_status="fail")
        self.assertIn("recomputation_quality_or_structure_invalid", self.verify(recomputed=changed)["reasons"])

    def test_prior_rate_warns_future_crisis_blocks(self):
        self.inputs["risk_free"]["rate_date"] = "2026-09-29"
        self.inputs["crisis_replay"]["holdings_as_of"] = "2026-10-01"
        self.rehash()
        result = self.verify()
        self.assertIn("risk_free_precedes_comparison_date", result["warnings"])
        self.assertIn("future_crisis_holdings", result["reasons"])

    def test_adapter_enforces_consistent_readonly_tenant_scope_before_reads(self):
        valid = {"tenant_id": TENANT, "read_only": "on", "isolation": "repeatable read"}
        for overrides in ({"tenant_id": "wrong"}, {"read_only": "off"}, {"isolation": "read committed"}):
            connection = Mock()
            connection.execute.return_value.fetchone.return_value = dict(valid, **overrides)
            with self.assertRaisesRegex(ValueError, "READ ONLY"):
                inspect_risk_output(connection, tenant_id=TENANT, subject_id="account-a",
                    as_of="2026-09-30", expected_comparison_date=date(2026, 9, 30))
            self.assertEqual(connection.execute.call_count, 1)

    def test_adapter_only_selects_and_never_commits(self):
        connection = Mock()
        connection.execute.return_value.fetchone.side_effect = [
            {"tenant_id": TENANT, "read_only": "on", "isolation": "repeatable read"}, self.existing]
        with patch("ibkr_etl.workflow_risk_evidence.risk.load_risk_inputs", return_value=(self.inputs, {})), \
             patch("ibkr_etl.workflow_risk_evidence.risk.compute_risk_snapshot", return_value=(self.payload, {}, risk_input_fingerprint(self.inputs))):
            result = inspect_risk_output(connection, tenant_id=TENANT, subject_id="account-a",
                as_of="2026-09-30", expected_comparison_date=date(2026, 9, 30))
        self.assertTrue(result["output_verified"])
        self.assertTrue(all(call.args[0].startswith("SELECT") for call in connection.execute.call_args_list))
        connection.commit.assert_not_called()
