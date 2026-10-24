"""Website comparisons with every catalog benchmark (WEBSITE_ALL_BENCHMARKS), on a real
PostgreSQL through the real benchmark writer. Skipped without AGENT_TEST_PG_ADMIN_DSN.

The subject's contract (GSPC primary, IXIC secondary) must come out exactly as before; the
other active catalog benchmarks are compared best effort, and only the website lists them.
Synthetic data only; prices and FX come from a fake provider.
"""
from __future__ import annotations

import dataclasses
import io
import os
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import test_medallion_layers_db as layers
from test_publish_gate_db import ADMIN_DSN, END, SESSIONS, START, SUBJECT, TENANT, Frame, _seed
from ibkr_etl import lineage, provisional_benchmark
from ibkr_etl.benchmark_analytics import QualityIssue
from ibkr_etl import publish_gate as gate
from ibkr_etl.db import connect

BROKEN = "000001.SS"  # Shanghai Composite: the provider has nothing, so it is skipped


class Ticker:
    def __init__(self, symbol):
        if symbol == BROKEN:
            raise RuntimeError("no data")
        self.symbol = symbol

    def history(self, **request):
        if self.symbol.endswith("=X"):
            return Frame([(day.isoformat(), 30.0) for day in SESSIONS])
        base = 100 + sum(map(ord, self.symbol)) % 50
        return Frame([(day.isoformat(), base * (1 + 0.0004 * index)) for index, day in enumerate(SESSIONS)])


def _late(symbol):
    """A provider whose history for one symbol stops a session before the NAV does."""

    class Late(Ticker):
        def history(self, **request):
            frame = super().history(**request)
            if self.symbol == symbol:
                frame = Frame(frame.rows[:-1])
            return frame

    return Late


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class WebsiteAllBenchmarksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = f"all_benchmarks_test_{uuid.uuid4().hex[:10]}"
        try:
            layers._build(cls.database)
            with layers._connect(cls.database) as owner:
                _seed(owner)
        except Exception:
            layers._drop(cls.database)
            raise
        cls.url = layers._url(cls.database, layers.OWNER)

    @classmethod
    def tearDownClass(cls):
        layers._drop(cls.database)

    def setUp(self):
        lineage.reset()
        with layers._connect(self.database) as owner:
            for table in ("performance_comparison_daily", "benchmark_prices", "reference_fx_rates",
                          "data_quality_results"):
                owner.execute(f"DELETE FROM {table}")

    def _write(self, flag, mode="off", break_it=None, ticker=None):
        job = connect(Path("/tmp/unused.sqlite3"), self.url, TENANT)
        environment = {"PORTFOLIO_TENANT_ID": TENANT, gate.MODE_ENV: mode,
                       provisional_benchmark.WEBSITE_ALL_BENCHMARKS_ENV: flag}
        original = provisional_benchmark.version_website_comparison

        def version(result):
            result = original(result)
            return break_it(result) if break_it else result

        try:
            with patch.dict(os.environ, environment), redirect_stdout(io.StringIO()), \
                    patch.object(provisional_benchmark, "version_website_comparison", version), \
                    patch.object(provisional_benchmark, "utc_now_iso",
                                 lambda: datetime.now(timezone.utc).isoformat()):
                return provisional_benchmark.refresh_and_materialize(
                    job, account_id=SUBJECT, start_date=START, end_date=END, ticker_factory=ticker or Ticker)
        finally:
            job.close()

    def _query(self, sql, *args):
        with layers._connect(self.database) as owner:
            return owner.execute(sql, args).fetchall()

    def _compared(self):
        return {row[0] for row in self._query(
            "SELECT DISTINCT benchmark_id FROM performance_comparison_daily WHERE subject_id = %s", SUBJECT)}

    def test_off_compares_only_the_contract(self):
        result = self._write("")
        self.assertEqual([item["benchmark_id"] for item in result["benchmarks"]], ["GSPC", "IXIC"])
        self.assertEqual(self._compared(), {"GSPC", "IXIC"})

    def test_on_adds_every_other_active_benchmark_and_skips_a_missing_one(self):
        catalog = {row[0] for row in self._query("SELECT benchmark_id FROM benchmark_master WHERE active")}
        result = self._write("on")
        by_id = {item["benchmark_id"]: item for item in result["benchmarks"]}
        self.assertEqual(set(by_id), catalog)
        self.assertEqual(by_id["GSPC"]["mapping_type"], "primary")
        self.assertEqual(by_id["IXIC"]["mapping_type"], "secondary")
        self.assertTrue(all(by_id[key]["mapping_type"] == "comparison" for key in catalog - {"GSPC", "IXIC"}))
        self.assertFalse(by_id["SHCOMP"]["available"])
        self.assertEqual(self._compared(), catalog - {"SHCOMP"})
        # Non-USD benchmarks (TWD, CNY, HKD, KRW) are converted to the account's USD.
        self.assertTrue({"TAIEX", "HSI", "KS200", "CSI300"} <= self._compared())

    def test_on_leaves_the_contract_fx_quality_log_and_mappings_alone(self):
        self._write("on")
        mappings = self._query("SELECT benchmark_id, mapping_type FROM portfolio_benchmark_mapping "
                               "WHERE subject_id = %s ORDER BY 1", SUBJECT)
        self.assertEqual(mappings, [("GSPC", "primary"), ("IXIC", "secondary")])
        # The USD account's contract needs no FX, so the comparisons store none.
        self.assertEqual(self._query("SELECT count(*) FROM reference_fx_rates")[0][0], 0)
        logged = {row[0] for row in self._query("SELECT DISTINCT split_part(run_reference, ':', 1) "
                                                "FROM data_quality_results WHERE subject_id = %s", SUBJECT)}
        self.assertLessEqual(logged, {"GSPC", "IXIC"})

    def test_contract_rows_are_identical_with_the_flag_on(self):
        sql = ("SELECT benchmark_id, observation_date, portfolio_value, benchmark_level, excess_return "
               "FROM performance_comparison_daily WHERE benchmark_id IN ('GSPC', 'IXIC') ORDER BY 1, 2")
        self._write("")
        before = self._query(sql)
        self._write("on")
        self.assertEqual(self._query(sql), before)

    def test_enforced_publish_gate_passes_with_the_comparisons(self):
        result = self._write("on", mode="enforce")
        self.assertEqual(result["status"], "success")

    def test_a_broken_comparison_never_holds_back_the_contract(self):
        def break_ewh(result):
            if result.benchmark_id != "EWH":
                return result
            return dataclasses.replace(result, rows=tuple(
                dataclasses.replace(row, benchmark_return=0.9, normalized_benchmark=row.normalized_benchmark * 1.9)
                if index == 20 else row for index, row in enumerate(result.rows)))

        result = self._write("on", mode="enforce", break_it=break_ewh)
        self.assertEqual(result["status"], "success")
        self.assertIn("GSPC", self._compared())

    def test_a_broken_contract_benchmark_is_still_held_back_with_the_switch_on(self):
        def break_gspc(result):
            if result.benchmark_id != "GSPC":
                return result
            return dataclasses.replace(result, rows=tuple(
                dataclasses.replace(row, excess_return=row.excess_return + 0.001) if index == 20 else row
                for index, row in enumerate(result.rows)))

        with self.assertRaises(gate.BuildHeldBack):
            self._write("on", mode="enforce", break_it=break_gspc)
        self.assertEqual(self._compared(), set())

    def test_a_contract_benchmark_behind_the_nav_raises_benchmark_not_current(self):
        # The provider has no close yet for the NAV's last session (as Yahoo's index history on
        # 22 Oct 2026, which had no 21 Oct close): only freshness fails, nothing is written, and
        # the message names both sessions.
        for flag in ("", "on"):
            with self.subTest(flag=flag):
                with self.assertRaises(provisional_benchmark.BenchmarkNotCurrent) as caught:
                    self._write(flag, ticker=_late("^GSPC"))
                message = str(caught.exception)
                self.assertIn("GSPC comparison failed data-quality checks: freshness", message)
                self.assertIn(SESSIONS[-2].isoformat(), message)
                self.assertIn(SESSIONS[-1].isoformat(), message)
                self.assertEqual(self._compared(), set())

    def test_a_late_comparison_benchmark_is_still_only_skipped(self):
        result = self._write("on", ticker=_late("EWH"))
        by_id = {item["benchmark_id"]: item for item in result["benchmarks"]}
        self.assertFalse(by_id["EWH"]["available"])
        self.assertIn("GSPC", self._compared())
        self.assertNotIn("EWH", self._compared())

    def test_freshness_with_any_other_failed_check_still_fails_the_job(self):
        original = provisional_benchmark.build_comparison

        def with_another_failure(**kwargs):
            result = original(**kwargs)
            if result.benchmark_id != "GSPC":
                return result
            extra = QualityIssue("common_dates", "fail", "made up for the test")
            return dataclasses.replace(result, quality_status="fail", issues=result.issues + (extra,))

        with patch.object(provisional_benchmark, "build_comparison", with_another_failure), \
                self.assertRaises(RuntimeError) as caught:
            self._write("", ticker=_late("^GSPC"))
        self.assertNotIsInstance(caught.exception, provisional_benchmark.BenchmarkNotCurrent)
        self.assertIn("freshness, common_dates", str(caught.exception))

    def test_website_lists_compared_catalog_benchmarks_after_the_contract(self):
        from psycopg.rows import dict_row

        from ibkr_etl import web

        self._write("on")
        with layers._connect(self.database, row_factory=dict_row) as owner, \
                patch.object(web, "TENANT_ID", TENANT):
            mapped = owner.execute(
                "SELECT benchmark_id, mapping_type, reporting_currency FROM portfolio_benchmark_mapping "
                "WHERE subject_id = %s", (SUBJECT,)).fetchall()
            extra = web._catalog_comparison_benchmarks(owner, SUBJECT, mapped)
        ids = [row["benchmark_id"] for row in extra]
        self.assertEqual(set(ids), self._compared() - {"GSPC", "IXIC"})
        self.assertTrue(all(row["mapping_type"] == "comparison" and row["reporting_currency"] == "USD"
                            for row in extra))


if __name__ == "__main__":
    unittest.main()
