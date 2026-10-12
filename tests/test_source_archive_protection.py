import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ibkr_etl.service import _archive_report, ingest_content


class SourceArchiveProtectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = SimpleNamespace(raw_report_dir=Path(self.folder.name), timezone=timezone.utc,
            azure_storage_account_url="", azure_raw_container="ibkr-raw", tenant_id="tenant",
            database_path=Path(self.folder.name) / "unused.sqlite", database_url="", tenant_name="test")
        self.clock = patch("ibkr_etl.service.datetime").start()
        self.clock.now.return_value = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.addCleanup(patch.stopall)

    def test_identical_duplicate_preserves_original_bytes_and_path(self):
        first = _archive_report(self.settings, b"original IBKR bytes", None, None)
        stat = Path(first[1]).stat()
        second = _archive_report(self.settings, b"original IBKR bytes", None, None)
        self.assertEqual(first, second)
        self.assertEqual(Path(first[1]).read_bytes(), b"original IBKR bytes")
        self.assertEqual(Path(first[1]).stat().st_mtime_ns, stat.st_mtime_ns)

    def test_conflicting_existing_archive_fails_without_overwriting_it(self):
        with patch("ibkr_etl.service.sha256_bytes", return_value="a" * 64):
            _, path = _archive_report(self.settings, b"original", None, None)
            with self.assertRaisesRegex(RuntimeError, "integrity failure"):
                _archive_report(self.settings, b"different", None, None)
        self.assertEqual(Path(path).read_bytes(), b"original")

    def test_cloud_source_upload_remains_no_overwrite(self):
        self.settings.azure_storage_account_url = "https://example.blob.core.windows.net"
        with patch("ibkr_etl.service.upload_bytes", return_value="https://example/source") as upload:
            _, location = _archive_report(self.settings, b"original", None, None)
            self.assertFalse(upload.call_args.kwargs["overwrite"])
            self.assertEqual(location, "https://example/source")

    def test_replay_rejects_mismatched_bytes_before_parsing_or_loading(self):
        path = Path(self.folder.name) / "original.xml"
        path.write_bytes(b"original")
        connection = Mock()
        with patch("ibkr_etl.service.initialize"), patch("ibkr_etl.service.connect", return_value=connection), \
             patch("ibkr_etl.service.start_run", return_value=1), patch("ibkr_etl.service.finish_run_failure"), \
             patch("ibkr_etl.service.parse_flex_xml") as parse, patch("ibkr_etl.service.load_report") as load:
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                ingest_content(self.settings, b"different", run_type="replay", existing_raw_path=path)
            parse.assert_not_called()
            load.assert_not_called()
            connection.close.assert_called_once()
        self.assertEqual(path.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
