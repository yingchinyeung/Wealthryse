from __future__ import annotations

import sqlite3
import unittest

from ibkr_etl.historical_reporting import (
    HistoricalReportingError,
    discover_historical_months,
    queue_report_generation_run,
    run_historical_month_end_batch,
)
from ibkr_etl.reporting_snapshot import ReportingSnapshotResult


class HistoricalReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE position_snapshots(account_id TEXT, snapshot_date TEXT);
            CREATE TABLE nav_snapshots(
                account_id TEXT, snapshot_date TEXT, asset_class TEXT
            );
            CREATE TABLE report_generation_runs(
                generation_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                template_id TEXT NOT NULL,
                template_version TEXT NOT NULL,
                status TEXT NOT NULL,
                output_reference TEXT,
                output_sha256 TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error_message TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def _insert_common_date(self, value: str) -> None:
        self.connection.execute(
            "INSERT INTO position_snapshots VALUES ('U1', ?)", (value,)
        )
        self.connection.execute(
            "INSERT INTO nav_snapshots VALUES ('U1', ?, 'TOTAL')", (value,)
        )

    def test_discovery_returns_last_common_date_and_calendar_period_end(self) -> None:
        for value in ("2025-01-21", "2025-01-31", "2025-02-27", "2025-02-28"):
            self._insert_common_date(value)
        months = discover_historical_months(
            self.connection,
            account_id="U1",
            start_date="2025-01-20",
            end_date="2025-02-28",
        )
        self.assertEqual(
            [(item.period_end, item.holdings_as_of) for item in months],
            [
                ("2025-01-31", "2025-01-31"),
                ("2025-02-28", "2025-02-28"),
            ],
        )

    def test_discovery_rejects_a_missing_reporting_month(self) -> None:
        self._insert_common_date("2025-01-31")
        with self.assertRaisesRegex(HistoricalReportingError, "2025-02"):
            discover_historical_months(
                self.connection,
                account_id="U1",
                start_date="2025-01-20",
                end_date="2025-02-28",
            )

    def test_batch_is_idempotent_and_reuses_price_cache(self) -> None:
        self._insert_common_date("2025-01-31")
        self._insert_common_date("2025-02-14")
        self._insert_common_date("2025-02-28")
        cache_ids: list[int] = []

        persisted_ids: list[int] = []

        def replay_refresher(
            connection, *, account_id, holdings_as_of, price_cache,
            persisted_price_symbols,
        ):
            cache_ids.append(id(price_cache))
            persisted_ids.append(id(persisted_price_symbols))
            return {"holdings_as_of": holdings_as_of}

        def snapshot_freezer(
            connection, *, tenant_id, subject_id, period_start,
            period_end, reporting_currency,
        ):
            return ReportingSnapshotResult(
                snapshot_id=f"snapshot-{period_end}",
                subject_id=subject_id,
                period_start=period_start,
                period_end=period_end,
                data_as_of=period_end,
                quality_status="warn",
                publication_allowed=False,
                publication_blockers=("primary_benchmark:not_licensed_for_publication",),
                data_sha256=f"digest-{period_end}",
                inserted=True,
            )

        arguments = dict(
            tenant_id="tenant-1",
            account_id="U1",
            period_start="2025-01-20",
            start_date="2025-01-20",
            end_date="2025-02-28",
            reporting_currency="USD",
            factsheet_template_id="factsheet",
            factsheet_template_version="factsheet-v1",
            presentation_template_id="presentation",
            presentation_template_version="presentation-v1",
            replay_refresher=replay_refresher,
            snapshot_freezer=snapshot_freezer,
        )
        first = run_historical_month_end_batch(self.connection, **arguments)
        second = run_historical_month_end_batch(self.connection, **arguments)
        count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM report_generation_runs"
        ).fetchone()["count"]
        self.assertEqual(first["months_completed"], 2)
        self.assertEqual(first["crisis_dates_completed"], 2)
        self.assertEqual(second["months_completed"], 2)
        self.assertEqual(count, 4)
        self.assertEqual(cache_ids, [cache_ids[0], cache_ids[0], cache_ids[2], cache_ids[2]])
        self.assertEqual(
            persisted_ids,
            [persisted_ids[0], persisted_ids[0], persisted_ids[2], persisted_ids[2]],
        )

    def test_batch_does_not_replay_unused_intra_month_snapshots(self) -> None:
        for value in ("2025-01-20", "2025-01-31"):
            self._insert_common_date(value)
        requested: list[str] = []

        def replay_refresher(*args, holdings_as_of, **kwargs):
            requested.append(holdings_as_of)
            return {"holdings_as_of": holdings_as_of}

        def snapshot_freezer(*args, period_end, subject_id, **kwargs):
            return ReportingSnapshotResult(
                snapshot_id=f"snapshot-{period_end}",
                subject_id=subject_id,
                period_start="2025-01-20",
                period_end=period_end,
                data_as_of=period_end,
                quality_status="pass",
                publication_allowed=True,
                publication_blockers=(),
                data_sha256=f"digest-{period_end}",
                inserted=True,
            )

        result = run_historical_month_end_batch(
            self.connection,
            tenant_id="tenant-1",
            account_id="U1",
            period_start="2025-01-20",
            start_date="2025-01-20",
            end_date="2025-01-31",
            reporting_currency="USD",
            factsheet_template_id="factsheet",
            factsheet_template_version="v1",
            presentation_template_id="presentation",
            presentation_template_version="v1",
            replay_refresher=replay_refresher,
            snapshot_freezer=snapshot_freezer,
        )

        self.assertEqual(requested, ["2025-01-31"])
        self.assertEqual(result["crisis_dates_completed"], 1)

    def test_batch_fails_if_replay_uses_wrong_holdings_date(self) -> None:
        self._insert_common_date("2025-01-31")

        def wrong_replay(*args, **kwargs):
            return {"holdings_as_of": "2025-02-01"}

        with self.assertRaisesRegex(HistoricalReportingError, "requested 2025-01-31"):
            run_historical_month_end_batch(
                self.connection,
                tenant_id="tenant-1",
                account_id="U1",
                period_start="2025-01-20",
                start_date="2025-01-20",
                end_date="2025-01-31",
                reporting_currency="USD",
                factsheet_template_id="factsheet",
                factsheet_template_version="v1",
                presentation_template_id="presentation",
                presentation_template_version="v1",
                replay_refresher=wrong_replay,
                snapshot_freezer=lambda *args, **kwargs: None,
            )

    def test_failed_report_run_is_requeued_in_place(self) -> None:
        run_id = queue_report_generation_run(
            self.connection,
            tenant_id="tenant-1",
            snapshot_id="snapshot-1",
            report_type="factsheet",
            template_id="factsheet",
            template_version="v1",
        )
        self.connection.execute(
            "UPDATE report_generation_runs SET status = 'failed', error_message = 'x'"
        )
        repeated = queue_report_generation_run(
            self.connection,
            tenant_id="tenant-1",
            snapshot_id="snapshot-1",
            report_type="factsheet",
            template_id="factsheet",
            template_version="v1",
        )
        row = self.connection.execute(
            "SELECT status, error_message FROM report_generation_runs"
        ).fetchone()
        self.assertEqual(repeated, run_id)
        self.assertEqual(row["status"], "queued")
        self.assertIsNone(row["error_message"])


if __name__ == "__main__":
    unittest.main()
