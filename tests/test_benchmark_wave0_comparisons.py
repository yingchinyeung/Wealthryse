"""The daily benchmark job's wave 0 check looks only at the account's contract benchmarks: the
website-only catalog comparisons (WEBSITE_ALL_BENCHMARKS) neither fail it nor change its evidence."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _job():
    spec = importlib.util.spec_from_file_location("refresh_provisional_benchmark",
                                                  ROOT / "scripts" / "refresh_provisional_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(benchmark_id, mapping_type, currency, fx_symbol, *, quality="warn", observations=10, rows=9):
    return {"benchmark_id": benchmark_id, "mapping_type": mapping_type, "symbol": "^" + benchmark_id,
            "native_currency": currency, "reporting_currency": "USD", "fx_symbol": fx_symbol,
            "fx_observations_used": 9, "maximum_fx_age_days": 0, "benchmark_observations": observations,
            "comparison_rows": rows, "quality_status": quality}


CONTRACT = [_item("TAIEX", "primary", "TWD", "TWDUSD=X"), _item("SHCOMP", "secondary", "CNY", "CNYUSD=X"),
            _item("HSI", "additional", "HKD", "HKDUSD=X")]
# Catalog comparisons are best effort: one in another currency came back without an FX series.
COMPARISONS = [_item("GSPC", "comparison", "USD", None, observations=40, rows=30),
               _item("KOSPI200", "comparison", "KRW", None, quality="fail", observations=7, rows=0)]


def _result(benchmarks):
    return {"account_id": "U16088606", "provider": "yfinance", "reporting_currency": "USD",
            "requested_end_date": "2026-10-22", "benchmarks": benchmarks,
            "benchmark_observations": sum(item["benchmark_observations"] for item in benchmarks),
            "comparison_rows": sum(item["comparison_rows"] for item in benchmarks)}


def _evidence(connection):
    calls = [call for call in connection.execute.call_args_list if "account_onboarding_checks" in call.args[0]]
    assert len(calls) == 1, calls
    sql, params = calls[0].args
    return sql, json.loads(params[-1])


class Wave0ComparisonsTests(unittest.TestCase):
    def setUp(self):
        self.job = _job()

    def test_catalog_comparisons_do_not_fail_the_check_or_change_its_evidence(self):
        alone, together = MagicMock(), MagicMock()
        self.job._mark_wave0_benchmark_fx_reconciled(alone, _result(CONTRACT))
        self.job._mark_wave0_benchmark_fx_reconciled(together, _result(CONTRACT + COMPARISONS))
        sql_alone, evidence_alone = _evidence(alone)
        sql_together, evidence_together = _evidence(together)
        self.assertEqual(evidence_together, evidence_alone)
        self.assertIn("status = 'pass'", sql_together)
        self.assertEqual([item["benchmark_id"] for item in evidence_together["benchmarks"]],
                         ["TAIEX", "SHCOMP", "HSI"])
        self.assertEqual((evidence_together["benchmark_observations"], evidence_together["comparison_rows"]),
                         (30, 27))

    def test_a_missing_contract_benchmark_still_fails(self):
        for benchmarks in (CONTRACT[:2] + COMPARISONS, CONTRACT[:2]):
            with self.assertRaisesRegex(RuntimeError, "did not cover the approved set"):
                self.job._mark_wave0_benchmark_fx_reconciled(MagicMock(), _result(benchmarks))

    def test_a_contract_benchmark_without_fx_still_fails(self):
        broken = [CONTRACT[0], CONTRACT[1], _item("HSI", "additional", "HKD", None)]
        with self.assertRaisesRegex(RuntimeError, "missing an FX series"):
            self.job._mark_wave0_benchmark_fx_reconciled(MagicMock(), _result(broken + COMPARISONS))

    def test_a_failed_contract_benchmark_is_a_warning_but_a_failed_comparison_is_not(self):
        connection = MagicMock()
        self.job._mark_wave0_benchmark_fx_reconciled(connection, _result(CONTRACT + COMPARISONS))
        self.assertIn("status = 'pass'", _evidence(connection)[0])
        connection = MagicMock()
        failed = [CONTRACT[0], CONTRACT[1], _item("HSI", "additional", "HKD", "HKDUSD=X", quality="fail")]
        self.job._mark_wave0_benchmark_fx_reconciled(connection, _result(failed + COMPARISONS))
        self.assertIn("status = 'warn'", _evidence(connection)[0])

    def test_other_accounts_are_untouched(self):
        connection = MagicMock()
        self.job._mark_wave0_benchmark_fx_reconciled(connection, {**_result(COMPARISONS), "account_id": "U1"})
        connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
