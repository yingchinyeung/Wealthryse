import copy
import unittest
from ibkr_etl.fx_source_evidence import trace_observation, rate_status
from ibkr_etl.util import canonical_json, sha256_text


class FxSourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        attrs = dict(fromCurrency="PHP", toCurrency="USD", rate="0", reportDate="20260930")
        fingerprint = sha256_text(canonical_json(attrs))
        self.rate = dict(account_id="a", rate_date="2026-09-30", from_currency="PHP", to_currency="USD",
                         rate_text="0", row_sha256=fingerprint, last_seen_run_id=1)
        self.source = dict(account_id="a", report_date="2026-09-30", record_type="ConversionRate",
            row_sha256=fingerprint, data_json=canonical_json(attrs), last_seen_run_id=1,
            ingestion_status="success", ingestion_completed_at="2026-10-01")

    def test_exact_stored_source_trace_is_not_raw_xml_certification(self):
        result = trace_observation(self.rate, [self.source])
        self.assertTrue(result["trace_verified"])
        self.assertFalse(result["raw_xml_verified"])
        self.assertEqual(result["parsed_source_status"], "nonpositive")

    def test_missing_or_ambiguous_source(self):
        for sources in ([], [self.source, self.source]):
            self.assertFalse(trace_observation(self.rate, sources)["trace_verified"])

    def test_tampering_and_wrong_scope_block(self):
        for key, value in [("account_id", "b"), ("report_date", "2026-09-29"), ("row_sha256", "bad")]:
            changed = dict(self.source, **{key: value})
            self.assertFalse(trace_observation(self.rate, [changed])["trace_verified"])

    def test_changed_normalized_rate_is_not_blameable_on_source(self):
        self.rate["rate_text"] = "-1"
        self.assertIn("normalization_mismatch", trace_observation(self.rate, [self.source])["issues"])

    def test_failed_ingestion_or_lineage_mismatch(self):
        for key, value in [("ingestion_status", "failed"), ("ingestion_completed_at", None), ("last_seen_run_id", 2)]:
            self.assertFalse(trace_observation(self.rate, [dict(self.source, **{key: value})])["trace_verified"])

    def test_numerical_status(self):
        for value, status in [("0", "nonpositive"), ("-1", "nonpositive"), ("NaN", "nonfinite"),
                              ("garbage", "malformed"), (".000001", "positive")]:
            self.assertEqual(rate_status(value), status)
