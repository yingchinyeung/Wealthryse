"""The daily benchmark job carries on past a subject that has no benchmark yet, or whose benchmark
has not yet published the close its NAV has reached, and fails on anything else."""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ibkr_etl import publish_gate
from ibkr_etl.provisional_benchmark import BenchmarkNotCurrent, NoBenchmarkContract, mapped_benchmarks

ROOT = Path(__file__).resolve().parents[1]


def _job():
    spec = importlib.util.spec_from_file_location("refresh_provisional_benchmark",
                                                  ROOT / "scripts" / "refresh_provisional_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENV = {"DEPLOYMENT_ENVIRONMENT": "staging", "ALLOW_UNLICENSED_BENCHMARK": "true",
       "DATABASE_URL": "postgresql://x", "BENCHMARK_ACCOUNT_IDS": "KIWOOM-1,U1"}


class NoBenchmarkContractTests(unittest.TestCase):
    def test_a_subject_without_a_mapping_raises_the_no_contract_error(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        with self.assertRaises(NoBenchmarkContract):
            mapped_benchmarks(connection, "KIWOOM-1")

    def _run(self, failure: Exception):
        job = _job()
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"subject_type": "physical_account"}

        def refresh(_connection, *, account_id, start_date):
            if account_id == "KIWOOM-1":
                raise failure
            return {"account_id": account_id, "benchmark_observations": 5, "comparison_rows": 5}

        with patch.dict(os.environ, ENV), \
                patch.object(job.input_readiness, "gate"), \
                patch.object(job, "connect", return_value=connection), \
                patch.object(job, "start_run", return_value=7), \
                patch.object(job, "refresh_and_materialize", side_effect=refresh), \
                patch.object(job, "_external_market_data_approved", return_value=False), \
                patch.object(job, "_refresh_crisis_scenarios", return_value={"status": "disabled"}), \
                patch.object(job, "finish_run_success") as success, \
                patch.object(job, "finish_run_failure") as failed:
            try:
                code = job.main()
            except RuntimeError as error:
                return None, error, success, failed
        return code, None, success, failed

    def test_the_other_subjects_refresh_and_the_run_reports_the_hold(self):
        code, error, success, failed = self._run(NoBenchmarkContract("No active benchmark contract is configured"))
        self.assertIsNone(error)
        self.assertEqual(code, publish_gate.EXIT_REJECTED)
        success.assert_called_once()
        self.assertIn('"held_back_by_publish_gate"', success.call_args.kwargs["reconciliation_json"])
        self.assertIn('"no_benchmark_contract"', success.call_args.kwargs["reconciliation_json"])
        self.assertIn('"U1"', success.call_args.kwargs["reconciliation_json"])
        failed.assert_not_called()

    def test_a_benchmark_behind_the_nav_holds_only_that_subject(self):
        code, error, success, failed = self._run(BenchmarkNotCurrent(
            "GSPC comparison failed data-quality checks: freshness (Latest common observation "
            "2026-10-20 does not match the required XNYS session 2026-10-21 (calendar).)"))
        self.assertIsNone(error)
        self.assertEqual(code, publish_gate.EXIT_REJECTED)
        success.assert_called_once()
        payload = success.call_args.kwargs["reconciliation_json"]
        self.assertIn('"benchmark_not_current"', payload)
        self.assertIn("2026-10-21", payload)
        self.assertIn('"U1"', payload)
        failed.assert_not_called()

    def test_any_other_failure_still_fails_the_run(self):
        _code, error, success, failed = self._run(RuntimeError("Benchmark data failed quality checks"))
        self.assertIsNotNone(error)
        success.assert_not_called()
        failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
