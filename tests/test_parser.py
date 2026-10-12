from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from ibkr_etl.config import Settings
from ibkr_etl.db import connect
from ibkr_etl.parser import FlexParseError, parse_flex_xml
from ibkr_etl.service import (
    backfill_windows,
    fetch_and_ingest,
    ingest_content,
    missing_position_snapshot_dates,
    monthly_windows,
)
from ibkr_etl.flex_client import FlexError, FlexReport


FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"


class ParserTests(unittest.TestCase):
    def test_parse_sample(self) -> None:
        report = parse_flex_xml(FIXTURE.read_bytes())
        self.assertEqual(report.statement_accounts, {"U1234567"})
        self.assertGreaterEqual(len(report.rows), 8)
        trade = next(row for row in report.rows if row.record_type == "Trade")
        self.assertEqual(trade.source_key, "ibExecID:0001.01")
        self.assertEqual(trade.report_date, "2026-08-18")

    def test_rejects_invalid_xml(self) -> None:
        with self.assertRaises(FlexParseError):
            parse_flex_xml(b"not xml")

    def test_backfill_windows_are_at_most_365_days(self) -> None:
        windows = backfill_windows(date(2024, 1, 1), date(2025, 12, 31))
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (date(2024, 1, 1), date(2024, 12, 30)))

    def test_monthly_windows_follow_calendar_months(self) -> None:
        windows = monthly_windows(date(2025, 1, 17), date(2025, 3, 5))
        self.assertEqual(
            windows,
            [
                (date(2025, 1, 17), date(2025, 1, 31)),
                (date(2025, 2, 1), date(2025, 2, 28)),
                (date(2025, 3, 1), date(2025, 3, 5)),
            ],
        )

    @patch("ibkr_etl.service.ingest_content")
    @patch("ibkr_etl.service.guarded_flex_request")
    @patch("ibkr_etl.service.FlexClient")
    def test_fetch_does_not_retry_statement_generation(
        self,
        client_class: Mock,
        guard: Mock,
        ingest: Mock,
    ) -> None:
        settings = Mock()
        client = client_class.return_value
        client.request_report.side_effect = FlexError(
            "Statement could not be generated", code="1001", phase="send_request"
        )
        guard.return_value.__enter__.return_value = {}

        with self.assertRaises(FlexError):
            fetch_and_ingest(
                settings, date(2026, 8, 20), date(2026, 8, 27), "test"
            )

        client.request_report.assert_called_once()
        ingest.assert_not_called()


class EndToEndTests(unittest.TestCase):
    def test_idempotent_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings(
                project_root=root,
                flex_token="",
                flex_query_id="",
                database_path=root / "portfolio.db",
                raw_report_dir=root / "raw",
                log_dir=root / "logs",
                timezone_name="Asia/Hong_Kong",
                daily_lookback_days=7,
                user_agent="test",
                request_timeout_seconds=10,
                max_wait_seconds=20,
            )
            first = ingest_content(settings, FIXTURE.read_bytes(), run_type="test")
            second = ingest_content(settings, FIXTURE.read_bytes(), run_type="test")
            self.assertEqual(first["reconciliation"]["status"], "PASS")
            self.assertEqual(second["reconciliation"]["status"], "PASS")

            connection = connect(settings.database_path)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT total_text FROM nav_snapshots").fetchone()[0], "5004.00")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0], 2)
            finally:
                connection.close()

    def test_missing_position_snapshot_dates_uses_source_nav_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings(
                project_root=root,
                flex_token="",
                flex_query_id="",
                database_path=root / "portfolio.db",
                raw_report_dir=root / "raw",
                log_dir=root / "logs",
                timezone_name="Asia/Hong_Kong",
                daily_lookback_days=7,
                user_agent="test",
                request_timeout_seconds=10,
                max_wait_seconds=20,
            )
            ingest_content(settings, FIXTURE.read_bytes(), run_type="test")
            connection = connect(settings.database_path)
            try:
                connection.execute(
                    """
                    INSERT INTO nav_snapshots(
                        account_id, snapshot_date, asset_class, currency,
                        total_text, row_sha256, last_seen_run_id
                    ) VALUES ('U1234567', '2026-08-19', 'TOTAL', 'USD',
                              '5005.00', 'additional-nav', 1)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            missing = missing_position_snapshot_dates(
                settings,
                date(2026, 8, 18),
                date(2026, 8, 19),
                "U1234567",
            )
            self.assertEqual(missing, [date(2026, 8, 19)])


if __name__ == "__main__":
    unittest.main()
