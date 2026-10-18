"""The certified-figures build's parts that need no database (tests/test_certified_build_db.py has
the rest). Synthetic values only."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import certified_build as build
from ibkr_etl import certified_figures as cf

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("build_certified_figures_unit",
                                                  ROOT / "scripts" / "build_certified_figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(figure_id: str, value, **columns) -> build.Row:
    return build.Row(subject_id="U9300001", figure_id=figure_id, as_of_date="2026-10-16",
                     source_table="silver.subject_nav_series", source_key={}, input_sha256="a" * 64,
                     calculation_version="v1", value=value, **columns)


class CertifiedBuildTests(unittest.TestCase):
    def test_off_unless_the_setting_is_on(self):
        script = _script()
        for environ in ({}, {cf.BUILD_SETTING: ""}, {cf.BUILD_SETTING: "true"}, {cf.BUILD_SETTING: "shadow"}):
            with self.subTest(environ=environ), patch.object(build, "run", side_effect=AssertionError), \
                    patch.object(script.lineage, "run_main", side_effect=AssertionError), \
                    contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(script.entry(["--backfill"], environ=environ), 0)
                self.assertIn("off", printed.getvalue())
        self.assertTrue(build.enabled({cf.BUILD_SETTING: " On "}))
        with patch.object(script.lineage, "run_main", return_value=0) as run_main:
            self.assertEqual(script.entry([], environ={cf.BUILD_SETTING: "on"}), 0)
        self.assertEqual(run_main.call_args.args[0], "ibkrv2stg-certified-figures")

    def test_the_imported_figures_name_their_source_contract_version(self):
        for name, version in build.SOURCE_CONTRACT_VERSIONS.items():
            layer, table = name.split(".")
            text = (ROOT / "infra" / "medallion" / "contracts" / layer / f"{table}.yaml").read_text(encoding="utf-8")
            self.assertEqual(re.search(r"^version:\s*(\S+)", text, re.M).group(1), version, name)

    def test_logs_mask_subjects(self):
        self.assertEqual(build.mask("U9300001"), "u…01")
        self.assertTrue(build.mask("synthetic-portfolio").startswith("s…"))
        self.assertNotIn("synthetic", build.mask("synthetic-portfolio"))

    def test_contract_rules(self):
        rows = [_row("stats.maximum_drawdown", 0.1), _row("stats.maximum_drawdown", -0.1),
                _row("risk.win_rate", 1.2), _row("benchmark.correlation", -1.0000001), _row("benchmark.correlation", 1.0),
                _row("stats.positive_months", None, status="unavailable")]
        self.assertEqual(build.rule_failures(rows), ["stats.maximum_drawdown", "risk.win_rate", "benchmark.correlation"])

    def test_only_new_and_changed_rows_are_written_and_a_change_without_cause_is_flagged(self):
        same, moved, caused, new = (_row("stats.skewness", 0.5), _row("stats.sortino_ratio", 1.5),
                                    _row("stats.calmar_ratio", 2.0), _row("stats.best_day", 0.04))
        caused.input_sha256 = "b" * 64

        def published(row, value):
            return {**{name: getattr(row, name) for name in build.COMPARED}, "value": value,
                    "input_sha256": "a" * 64, "calculation_version": "v1"}

        result = build.compare([same, moved, caused, new], {
            same.key: published(same, 0.5), moved.key: published(moved, 1.0), caused.key: published(caused, 1.0)})
        self.assertEqual((result["new"], result["changed"], result["unchanged"], result["violations"]),
                         ([new], [moved, caused], 1, [moved]))

    def test_dates_due_every_run(self):
        subject = build.Subject("U9300001", {"reporting_currency": "USD"},
                                [f"2026-10-{day:02d}" for day in range(1, 17)])
        last_five = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
        self.assertEqual(build.due_dates(subject, []), last_five)            # nothing published: the rest is history
        self.assertEqual(build.due_dates(subject, ["2026-10-14"]), last_five)
        self.assertEqual(build.due_dates(subject, subject.dates[:5]), subject.dates[5:])
        self.assertEqual(build.expected_date(date(2026, 10, 19)), date(2026, 10, 16))  # Monday: Friday's data

    def test_history_is_the_older_dates_without_figures_newest_first(self):
        subject = build.Subject("U9300001", {"reporting_currency": "USD"},
                                [f"2026-10-{day:02d}" for day in range(1, 17)])
        published = subject.dates[8:]                                         # 9 to 16 Oct
        due = build.due_dates(subject, published)
        self.assertEqual(build.history_dates(subject, published, due, backfill=False),
                         [f"2026-10-{day:02d}" for day in range(8, 0, -1)])
        self.assertEqual(build.history_dates(subject, published, due, backfill=True),
                         [f"2026-10-{day:02d}" for day in range(11, 0, -1)])     # every older date again

    def test_the_plan_reads_due_dates_first_then_history_one_subject_at_a_time(self):
        one = build.Subject("U9300001", {}, ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
                                             "2026-10-08", "2026-10-09"])
        two = build.Subject("test-portfolio", {}, ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"])
        plan = [(subject.id, day) for subject, day in build.work_plan([one, two], {}, backfill=False)]
        self.assertEqual(plan[:9], [("U9300001", day) for day in ("2026-10-05", "2026-10-06", "2026-10-07",
                                                                  "2026-10-08", "2026-10-09")]
                         + [("test-portfolio", day) for day in ("2026-10-06", "2026-10-07", "2026-10-08",
                                                                "2026-10-09")])
        self.assertEqual(plan[9:], [("U9300001", "2026-10-02"), ("U9300001", "2026-10-01")])
        # Once 2 Oct onwards is published, only 1 Oct is history; the due dates stay the last five.
        plan = build.work_plan([one], {"U9300001": one.dates[1:]}, backfill=False)
        self.assertEqual([day for _, day in plan], ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
                                                    "2026-10-09", "2026-10-01"])
        # After days without a run, the new dates follow on from the published ones, so a run that
        # stops among them leaves no gap.
        plan = build.work_plan([one], {"U9300001": one.dates[:2]}, backfill=False)
        self.assertEqual([day for _, day in plan], one.dates[2:])

    def test_time_limit(self):
        self.assertEqual(build.time_limit(None, {}), build.DEFAULT_MINUTES)
        self.assertEqual(build.time_limit(None, {build.MINUTES_ENV: "12.5"}), 12.5)
        self.assertEqual(build.time_limit(None, {build.MINUTES_ENV: "soon"}), build.DEFAULT_MINUTES)
        self.assertEqual(build.time_limit(5, {build.MINUTES_ENV: "12.5"}), 5)
        for bad in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                build.time_limit(bad, {})
        self.assertEqual(_script()._arguments(["--minutes", "7"]).minutes, 7.0)
        self.assertIsNone(_script()._arguments([]).minutes)

    def test_freshness_follows_the_account_header(self):
        subject = build.Subject("U9300001", {"reporting_currency": "USD"}, ["2026-10-14", "2026-10-15", "2026-10-16"])
        pages = build.Pages(None, subject, "2026-10-16")
        texts = {}
        for today in (date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)):
            [row] = build.freshness_row(pages, today)
            texts[today.isoformat()] = row.value_text
        self.assertEqual(texts, {"2026-10-19": "current", "2026-10-20": "one business day behind",
                                 "2026-10-21": "overdue"})
        self.assertEqual(build.freshness_row(build.Pages(None, subject, "2026-10-15"), date(2026, 10, 19)), [])


if __name__ == "__main__":
    unittest.main()
