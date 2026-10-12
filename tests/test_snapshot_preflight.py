import hashlib
import unittest
from unittest.mock import Mock, patch, MagicMock
from ibkr_etl.snapshot_preflight import archive_blob_location, compare_records, inspect_archived_xml, preflight_snapshot
from ibkr_etl.util import canonical_json, sha256_text


class SnapshotPreflightTests(unittest.TestCase):
    def test_preflight_refuses_writable_inconsistent_or_wrong_tenant_session(self):
        responses = [
            [{"transaction_read_only": "off"}],
            [{"transaction_read_only": "on"}, {"transaction_isolation": "read committed"}],
            [{"transaction_read_only": "on"}, {"transaction_isolation": "repeatable read"}, {"tenant": "OTHER"}],
        ]
        for response in responses:
            connection = MagicMock()
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.execute.return_value.fetchone.side_effect = response
            with patch("ibkr_etl.snapshot_preflight.validate_combined"), self.assertRaises(ValueError):
                preflight_snapshot(connection, {"evidence": {"account_id": "A"}}, tenant_id="T")
            connection.commit.assert_not_called()

    def compare(self, source, destination, **kwargs):
        return compare_records(source, destination, keys=("id",), columns=("id", "value"), **kwargs)

    def test_counts_missing_conflicting_and_preserved_extra(self):
        result = self.compare([{"id": "a", "value": "1"}, {"id": "b", "value": "2"}],
                              [{"id": "a", "value": "9"}, {"id": "c", "value": "3"}])
        self.assertEqual((result["missing_rows"], result["conflicting_rows"], result["destination_extra_rows"]), (1, 1, 1))
        self.assertEqual(result["conflicting_columns"], {"value": 1})
        self.assertNotIn("9", str(result))

    def test_same_count_value_difference_not_passed(self):
        result = self.compare([{"id": "a", "value": "1"}], [{"id": "a", "value": "2"}])
        self.assertEqual(result["matching_rows"], 0)
        self.assertEqual(result["conflicting_rows"], 1)

    def test_duplicate_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.compare([{"id": "a", "value": "1"}] * 2, [])

    def test_equivalent_json_matches_but_malformed_json_conflicts(self):
        before = [{"id": "a", "value": '{"x":"1"}'}]
        same = self.compare(before, [{"id": "a", "value": '{ "x": "1" }'}], json_columns=("value",))
        self.assertEqual(same["matching_rows"], 1)
        broken = self.compare(before, [{"id": "a", "value": 'not-json'}], json_columns=("value",))
        self.assertEqual(broken["conflicting_rows"], 1)

    def xml(self, body, **kwargs):
        content = ('<FlexQueryResponse><FlexStatements><FlexStatement accountId="A" fromDate="20250120" toDate="20250120">'
                   + body + '</FlexStatement></FlexStatements></FlexQueryResponse>').encode()
        return inspect_archived_xml(content, expected_sha256=hashlib.sha256(content).hexdigest(), accounts={"A"}, **kwargs)

    def test_absent_transfers_not_treated_as_empty(self):
        result = self.xml("<ConversionRates/>")
        self.assertEqual(result["statements"][0]["transfers_section"], "missing")
        self.assertEqual(result["source_completeness"], "unverified")

    def test_present_empty_transfers_recorded_without_full_completeness(self):
        result = self.xml("<Transfers/>")
        self.assertEqual(result["statements"][0]["transfers_section"], "present_empty")
        self.assertTrue(result["sample_only"])

    def test_transfer_records_counted_not_exposed(self):
        result = self.xml('<Transfers><Transfer amount="987654"/></Transfers>')
        self.assertEqual(result["statements"][0]["transfer_records"], 1)
        self.assertNotIn("987654", str(result))

    def test_known_bad_fx_matches_exact_hash_and_value(self):
        attrs = {"fromCurrency": "PHP", "toCurrency": "USD", "rate": "0", "reportDate": "20250120"}
        known = {("A", "2025-01-20", sha256_text(canonical_json(attrs))): "0"}
        result = self.xml('<ConversionRates><ConversionRate fromCurrency="PHP" toCurrency="USD" rate="0" reportDate="20250120"/></ConversionRates>', invalid_identities=known)
        self.assertEqual(result["matched_invalid_source_values"], 1)
        known[next(iter(known))] = "-1"
        mismatch = self.xml('<ConversionRate fromCurrency="PHP" toCurrency="USD" rate="0" reportDate="20250120"/>', invalid_identities=known)
        self.assertEqual(mismatch["matched_invalid_row_hashes"], 1)
        self.assertEqual(mismatch["matched_invalid_source_values"], 0)

    def test_archive_hash_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            inspect_archived_xml(b"<FlexQueryResponse/>", expected_sha256="0"*64, accounts={"A"})

    def test_dtd_entity_expansion_rejected(self):
        content = b'<!DOCTYPE root [<!ENTITY x "secret">]><root>&x;</root>'
        with self.assertRaisesRegex(ValueError, "DTD"):
            inspect_archived_xml(content, expected_sha256=hashlib.sha256(content).hexdigest(), accounts={"A"})

    def test_other_accounts_not_exposed(self):
        content = b'<FlexStatement accountId="OTHER"><Transfers><Transfer amount="secret"/></Transfers></FlexStatement>'
        result = inspect_archived_xml(content, expected_sha256=hashlib.sha256(content).hexdigest(), accounts={"A"})
        self.assertEqual(result["statements"], [])

    def test_blob_scope_enforced(self):
        base = "https://approved.blob.core.windows.net/raw-statements/a/file.xml"
        self.assertEqual(archive_blob_location(base, expected_host="approved.blob.core.windows.net"), ("raw-statements", "a/file.xml"))
        for url in (base.replace("approved.", "evil."), base+"?sig=secret", base.replace("raw-statements", "other"),
                    base.replace("/a/", "/%2e%2e/"), base.replace("https", "http")):
            with self.assertRaises(ValueError):
                archive_blob_location(url, expected_host="approved.blob.core.windows.net")
