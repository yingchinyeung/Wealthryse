from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ibkr_etl.config import Settings
from ibkr_etl.report import (
    _daily_modified_dietz_returns,
    _performance_metrics,
    generate_daily_report,
)
from ibkr_etl.service import ingest_content


FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"


class ReportTests(unittest.TestCase):
    def test_generates_latest_and_dated_self_contained_dashboard(self) -> None:
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
            latest, archive = generate_daily_report(
                settings.database_path,
                root / "reports",
                generated_at=datetime(
                    2026, 8, 19, 16, 0, tzinfo=timezone(timedelta(hours=8))
                ),
            )

            self.assertTrue(latest.is_file())
            self.assertTrue(archive.is_file())
            document = latest.read_text(encoding="utf-8")
            self.assertIn("IBKR Portfolio Daily Analytics", document)
            self.assertIn("U1234567", document)
            self.assertIn("application/json", document)
            self.assertIn("Performance & risk", document)
            self.assertIn("Sector allocation", document)
            self.assertNotIn("Profit factor", document)
            self.assertNotIn("profit_factor", document)
            self.assertIn("BLUE mean daily return", document)
            self.assertNotIn("IBKR_FLEX_TOKEN", document)

    def test_daily_risk_metrics_adjust_flows_and_reject_discontinuities(self) -> None:
        nav = [
            {"date": "2026-01-01", "total": 100.0},
            {"date": "2026-01-02", "total": 210.0},
            {"date": "2026-01-03", "total": 220.0},
            {"date": "2026-01-04", "total": 50_220.0},
        ]
        flows = {"2026-01-02": 100.0}
        returns = _daily_modified_dietz_returns(nav, flows)
        self.assertEqual(2, len(returns))
        self.assertAlmostEqual(10.0 / 150.0, returns[0]["return"])
        self.assertAlmostEqual(10.0 / 210.0, returns[1]["return"])

        metrics = _performance_metrics(
            nav,
            flows,
            {
                "annual_rate": 0.04,
                "observation_date": "2026-01-02",
                "source": "test",
            },
            None,
        )
        self.assertEqual(2, metrics["observations"])
        self.assertEqual(1, metrics["excluded_observations"])
        self.assertIsNotNone(metrics["annualized_volatility_pct"])
        self.assertIsNotNone(metrics["sharpe_ratio"])


if __name__ == "__main__":
    unittest.main()
