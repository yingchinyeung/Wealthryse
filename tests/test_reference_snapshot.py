import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError, ResourceModifiedError, HttpResponseError
from ibkr_etl.cloud_storage import download_blob_snapshot
from ibkr_etl.reference_snapshot import inspect_reference_bytes, load_reference_snapshot
from ibkr_etl.derived_portfolio import refresh_filtered_portfolio

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reference_worker_test", ROOT / "scripts/run_self_service_derived_refresh.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
CONTENT = b'{"version":2,"risk_free":{},"sectors":{"123":{"country":"Taiwan","country_checked":true}}}'


class ReferenceSnapshotTests(unittest.TestCase):
    def test_exact_bytes_bound_and_no_financial_certification(self):
        data, manifest = inspect_reference_bytes(CONTENT)
        self.assertEqual(data["sectors"]["123"]["country"], "Taiwan")
        self.assertEqual(manifest["content_sha256"], hashlib.sha256(CONTENT).hexdigest())
        self.assertTrue(manifest["input_format_verified"])
        self.assertFalse(manifest["financial_accuracy_verified"])
        self.assertFalse(manifest["historical_classification_coverage_verified"])
        self.assertNotIn("Taiwan", json.dumps(manifest))

    def test_byte_and_same_count_classification_changes_invalidate(self):
        _, first = inspect_reference_bytes(CONTENT)
        _, whitespace = inspect_reference_bytes(CONTENT + b"\n")
        _, changed = inspect_reference_bytes(CONTENT.replace(b"Taiwan", b"China"))
        self.assertNotEqual(first["content_sha256"], whitespace["content_sha256"])
        self.assertEqual(first["semantic_sha256"], whitespace["semantic_sha256"])
        self.assertNotEqual(first["semantic_sha256"], changed["semantic_sha256"])

    def test_invalid_cache_never_becomes_empty(self):
        for content in (b"", b"bad", b"[]", b"{}", b'\xff',
                        b'{"risk_free":{},"sectors":[],"version":2}',
                        b'{"risk_free":{},"sectors":{},"version":true}',
                        b'{"risk_free":{},"sectors":{},"version":9}',
                        b'{"risk_free":{},"sectors":{},"sectors":{}}',
                        b'{"risk_free":{"USD":{"rate":NaN}},"sectors":{}}',
                        b'{"risk_free":{"USD":{"rate":1e999}},"sectors":{}}',
                        CONTENT.replace(b'true', b'"true"'),
                        CONTENT.replace(b'"Taiwan"', b'null')):
            with self.subTest(content=content), self.assertRaises(ValueError):
                inspect_reference_bytes(content)

    def test_size_and_external_hash_required_to_match(self):
        with patch("ibkr_etl.reference_snapshot.MAX_BYTES", 10), self.assertRaises(ValueError):
            inspect_reference_bytes(CONTENT)
        for expected in ("a" * 64, "bad", True):
            with self.assertRaises(ValueError): inspect_reference_bytes(CONTENT, expected_sha256=expected)
        inspect_reference_bytes(CONTENT, expected_sha256=hashlib.sha256(CONTENT).hexdigest())

    def test_missing_file_stops_before_any_database_use(self):
        connection = Mock()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): refresh_filtered_portfolio(connection, Path(folder)/"missing.json")
        connection.execute.assert_not_called()

    def test_file_changed_since_download_stops_before_database_use(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"ref.json";path.write_bytes(CONTENT)
            expected = hashlib.sha256(CONTENT).hexdigest()
            path.write_bytes(CONTENT.replace(b"Taiwan", b"China"))
            connection = Mock()
            with self.assertRaises(ValueError):
                refresh_filtered_portfolio(connection, path, expected_reference_sha256=expected)
            connection.execute.assert_not_called()

    def test_legacy_version_and_bom_supported_without_empty_substitution(self):
        data, _ = inspect_reference_bytes(b'\xef\xbb\xbf{"risk_free":{},"sectors":{}}')
        self.assertEqual(data, {"risk_free": {}, "sectors": {}})

    def test_loader_reads_exact_validated_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"ref.json";path.write_bytes(CONTENT)
            self.assertEqual(load_reference_snapshot(path), inspect_reference_bytes(CONTENT))


class BlobSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.client = self.service.get_blob_client.return_value
        self.client.get_blob_properties.return_value = SimpleNamespace(
            size=len(CONTENT), etag='"etag-one"', version_id="version-one")
        self.client.download_blob.return_value.readall.return_value = CONTENT

    def read(self):
        with patch("ibkr_etl.cloud_storage._service", return_value=self.service):
            return download_blob_snapshot("https://storage", "archive", "reference.json")

    def test_pins_version_and_etag_with_bounded_content(self):
        content, receipt = self.read()
        self.assertEqual(content, CONTENT)
        self.service.get_blob_client.assert_called_with(container="archive", blob="reference.json", version_id="version-one")
        self.client.download_blob.assert_called_once_with(etag='"etag-one"',
            match_condition=MatchConditions.IfNotModified, max_concurrency=1)
        self.assertEqual(receipt["content_sha256"], hashlib.sha256(CONTENT).hexdigest())

    def test_unversioned_cache_still_etag_fenced(self):
        self.client.get_blob_properties.return_value.version_id = None
        self.assertIsNone(self.read()[1]["version_id"])
        self.assertEqual(self.service.get_blob_client.call_count, 1)

    def test_only_property_not_found_permits_fallback(self):
        self.client.get_blob_properties.side_effect = ResourceNotFoundError()
        self.assertIsNone(self.read())
        self.client.get_blob_properties.side_effect = HttpResponseError()
        with self.assertRaises(HttpResponseError): self.read()

    def test_overwrite_or_download_disappearance_fails_closed(self):
        for error in (ResourceModifiedError(), ResourceNotFoundError()):
            self.client.download_blob.side_effect = error
            with self.assertRaises(type(error)): self.read()

    def test_invalid_size_and_missing_etag_rejected_before_download(self):
        for size in (0, 9 * 1024 * 1024):
            self.client.get_blob_properties.return_value.size = size
            with self.assertRaises(ValueError): self.read()
        self.client.get_blob_properties.return_value.size = len(CONTENT)
        self.client.get_blob_properties.return_value.etag = None
        with self.assertRaises(ValueError): self.read()
        self.client.download_blob.assert_not_called()

    def test_download_length_must_match_pinned_properties(self):
        self.client.download_blob.return_value.readall.return_value = CONTENT[:-1]
        with self.assertRaises(ValueError): self.read()


class WorkerReferenceTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(azure_storage_account_url="https://website", azure_report_container="reports", tenant_id="tenant")
        self.metadata = {"content_sha256": hashlib.sha256(CONTENT).hexdigest(), "version_id": "version", "etag": "one"}

    def test_missing_primary_can_use_explicit_legacy_and_records_exact_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"REFERENCE_STORAGE_ACCOUNT_URL": "https://legacy"}), \
                patch.object(worker, "download_blob_snapshot", side_effect=[None, (CONTENT, self.metadata)]) as download:
            path = Path(folder)/"ref.json"
            receipt = worker._download_reference_data(self.settings, path)
            self.assertEqual(path.read_bytes(), CONTENT)
            self.assertEqual(receipt["content_sha256"], self.metadata["content_sha256"])
            self.assertEqual(receipt["storage"]["version_id"], "version")
            self.assertEqual(receipt["source"], "legacy-production")
            self.assertEqual(download.call_count, 2)

    def test_malformed_or_denied_primary_cannot_fall_back_or_replace_local_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"REFERENCE_STORAGE_ACCOUNT_URL": "https://legacy"}):
            path = Path(folder)/"ref.json";path.write_bytes(CONTENT)
            for outcome in (ValueError("denied"), (b"bad", {"content_sha256": hashlib.sha256(b"bad").hexdigest()})):
                with patch.object(worker, "download_blob_snapshot", side_effect=[outcome]) as download, self.assertRaises(ValueError):
                    worker._download_reference_data(self.settings, path)
                self.assertEqual(path.read_bytes(), CONTENT)
                self.assertEqual(download.call_count, 1)

    def test_run_watermark_contains_reference_binding(self):
        connection = Mock()
        worker._start_run(connection, dict(subject_id="filtered", definition_id="definition", effective_from="2025-01-20",
            configuration_sha256="a"*64), "2026-10-05", self.metadata)
        watermark = json.loads(connection.execute.call_args[0][1][-1])
        self.assertEqual(watermark["reference_input"], self.metadata)


if __name__ == "__main__": unittest.main()
