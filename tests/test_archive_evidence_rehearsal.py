import hashlib
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from scripts.rehearse_archive_evidence import StorageReadCredential

from ibkr_etl.archive_evidence_rehearsal import EvidenceRehearsal, reused_archive_runs
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.util import canonical_json


def document(rate="0.128", extra=""):
    return (f'<FlexStatement accountId="A" fromDate="20250120" toDate="20250120"><Transfers/>'
        f'<ConversionRates><ConversionRate fromCurrency="HKD" toCurrency="USD" rate="{rate}"/>'
        f'{extra}</ConversionRates></FlexStatement>').encode()


def expected(content, run=1):
    row = next(r for r in parse_flex_xml(content).rows if r.record_type == "ConversionRate")
    normalized = dict(account_id="A", rate_date="2025-01-20", from_currency="HKD", to_currency="USD",
        rate_text=row.attributes["rate"], row_sha256=row.row_sha256, last_seen_run_id=run)
    legacy = dict(account_id="A", report_date="2025-01-20", section="ConversionRates", record_type="ConversionRate",
        source_key="derived:legacy", data_json=canonical_json(row.attributes), row_sha256=row.row_sha256, last_seen_run_id=run)
    return normalized, legacy


def add(rehearsal, content, run=1):
    return rehearsal.add_archive(content, expected_sha256=hashlib.sha256(content).hexdigest(), run_id=run)


class ArchiveRehearsalTests(unittest.TestCase):
    def test_repeated_ingestion_requires_exact_successful_tenant_digest(self):
        archive = [dict(run_id=1, content_sha256="digest", tenant_id="T")]
        run = dict(run_id=2, raw_sha256="digest", tenant_id="T", status="success", completed_at="time")
        self.assertEqual(reused_archive_runs(archive_rows=archive, source_runs=[run], required_runs={2}, tenant_id="T"), {"digest": [2]})
        for change in ({"raw_sha256": "other"}, {"status": "failed"}, {"tenant_id": "other"}, {"completed_at": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                reused_archive_runs(archive_rows=archive, source_runs=[{**run, **change}], required_runs={2}, tenant_id="T")

    def test_reused_archive_preserves_distinct_source_runs_not_duplicate_versions(self):
        r = self.rehearsal(run=2)
        add(r, document(), run=1)
        add(r, document(), run=2)
        self.assertEqual(r.summary()["archives_verified"], 1)
        self.assertEqual(r.summary()["source_run_archive_links_verified"], 2)
        self.assertEqual(r.summary()["content_versions"], 1)
        self.assertEqual(r.summary()["normalized_fx_verified"], 1)

    def test_storage_token_is_cached_across_concurrent_reads(self):
        with patch("scripts.rehearse_archive_evidence.AzureCliCredential") as delegate:
            token = Mock(expires_on=10**12)
            delegate.return_value.get_token.return_value = token
            credential = StorageReadCredential()
            with ThreadPoolExecutor(max_workers=4) as pool:
                tokens = list(pool.map(lambda _: credential.get_token("https://storage.azure.com/.default"), range(8)))
            self.assertTrue(all(t is token for t in tokens))
            delegate.return_value.get_token.assert_called_once()

    def test_storage_credential_rejects_other_scopes_and_refreshes_expiry(self):
        with patch("scripts.rehearse_archive_evidence.AzureCliCredential") as delegate:
            delegate.return_value.get_token.side_effect = [Mock(expires_on=0), Mock(expires_on=10**12)]
            credential = StorageReadCredential()
            with self.assertRaises(ValueError):
                credential.get_token("https://management.azure.com/.default")
            delegate.return_value.get_token.assert_not_called()
            credential.get_token("https://storage.azure.com/.default")
            credential.get_token("https://storage.azure.com/.default")
            self.assertEqual(delegate.return_value.get_token.call_count, 2)

    def rehearsal(self, content=None, run=1):
        n, l = expected(content or document(), run)
        return EvidenceRehearsal(accounts={"A"}, normalized=[n], legacy=[l])

    def test_alias_points_to_one_canonical_version_and_replay_is_idempotent(self):
        r = self.rehearsal()
        self.assertTrue(add(r, document()))
        summary = r.summary()
        self.assertEqual(summary["normalized_fx_verified"], 1)
        self.assertEqual(summary["legacy_aliases_verified"], 1)
        self.assertEqual(summary["content_versions"], 1)
        self.assertFalse(add(r, document()))
        self.assertEqual(summary, r.summary())

    def test_corrections_retain_old_versions_without_changing_current_values(self):
        r = self.rehearsal(document("0.129"), run=2)
        add(r, document(), run=1)
        self.assertEqual(r.summary()["normalized_fx_verified"], 0)
        add(r, document("0.129"), run=2)
        self.assertEqual(r.summary()["canonical_keys"], 1)
        self.assertEqual(r.summary()["content_versions"], 2)
        self.assertEqual(r.summary()["normalized_fx_verified"], 1)

    def test_exact_source_run_is_required(self):
        r = self.rehearsal()
        add(r, document(), run=2)
        self.assertEqual(r.summary()["normalized_fx_verified"], 0)
        self.assertEqual(r.summary()["legacy_aliases_verified"], 0)

    def test_bad_hash_and_conflict_leave_no_partial_evidence(self):
        r = self.rehearsal()
        before = r.summary()
        with self.assertRaises(ValueError):
            r.add_archive(document(), expected_sha256="bad", run_id=1)
        with self.assertRaises(ValueError):
            add(r, document(extra='<ConversionRate fromCurrency="HKD" toCurrency="USD" rate="0.129"/>'))
        self.assertEqual(before, r.summary())

    def test_invalid_rates_are_preserved_and_matched_not_repaired(self):
        r = self.rehearsal(document("0"))
        add(r, document("0"))
        self.assertEqual(r.summary()["invalid_fx_verified"], 1)
        self.assertFalse(r.summary()["financial_values_changed"])

    def test_missing_or_tampered_legacy_evidence_rejected(self):
        n, l = expected(document())
        with self.assertRaises(ValueError):
            EvidenceRehearsal(accounts={"A"}, normalized=[n], legacy=[{**l, "row_sha256": "bad"}])
        with self.assertRaises(ValueError):
            EvidenceRehearsal(accounts={"B"}, normalized=[n], legacy=[l])
        r = self.rehearsal()
        self.assertEqual(r.summary()["legacy_aliases_unmatched"], 1)

    def test_duplicate_normalized_or_legacy_identities_rejected(self):
        n, l = expected(document())
        with self.assertRaises(ValueError):
            EvidenceRehearsal(accounts={"A"}, normalized=[n,n], legacy=[l])
        with self.assertRaises(ValueError):
            EvidenceRehearsal(accounts={"A"}, normalized=[n], legacy=[l,l])

    def test_dtd_and_cross_account_observations_do_not_enter_rehearsal(self):
        r = self.rehearsal()
        with self.assertRaises(ValueError):
            add(r, b'<!DOCTYPE FlexStatement>'+document())
        add(r, document().replace(b'accountId="A"', b'accountId="B"'))
        self.assertEqual(r.summary()["content_versions"], 0)
        self.assertEqual(r.summary()["normalized_fx_verified"], 0)
