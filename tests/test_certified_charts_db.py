"""The charts on certified figures (ibkr_etl.certified_charts) on a real PostgreSQL.

Needs a disposable server: set AGENT_TEST_PG_ADMIN_DSN to a superuser DSN (CI starts one).
Skipped otherwise. Uses the certified build's test database (tests/test_certified_build_db.py):
one synthetic account (U9300001) with four months of NAV, two holdings, cash, a synthetic
benchmark and a risk snapshot. The rolling beta window is shortened to 20 common dates so the
four months have points. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import functools
import io
import math
import os
import unittest
from unittest.mock import patch

import test_certified_build_db as base
import test_medallion_layers_db as layers
from ibkr_etl import certified_build as build
from ibkr_etl import certified_charts as charts
from ibkr_etl import certified_figures as cf

SUBJECT, FIRST, LAST = base.SUBJECT, base.FIRST, base.LAST
LOGGER = "uvicorn.error.certified_figures"
ADMIN_DSN = base.ADMIN_DSN


class _ChartsDatabase(base._BuildDatabase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        original = cls.web.rolling_benchmark_statistics
        cls.rolling = patch.object(cls.web, "rolling_benchmark_statistics", functools.partial(original, window=20))
        cls.rolling.start()

    @classmethod
    def tearDownClass(cls):
        cls.rolling.stop()
        super().tearDownClass()

    def _charts_run(self, mode="fallback", **kwargs):
        with patch.dict(os.environ, {charts.SETTING: mode}):
            return self._run(**kwargs)

    def _chart_rows(self) -> dict[tuple, dict]:
        return {key: row for key, row in self._published().items() if key[1] in charts.FIGURES}

    def _lines(self, captured) -> list[dict]:
        import json

        return [json.loads(message.split(":", 2)[2].partition(" ")[2]) for message in captured.output]


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ChartsBuildTests(_ChartsDatabase):
    """In order: off reads no chart; fallback publishes them; nothing new; the website; a differing
    point; the rehearsal; reader errors in fallback and on."""

    def test_1_with_the_setting_off_the_build_reads_no_chart(self):
        code, lines = self._run()
        self.assertEqual(code, 0, lines)
        [(_, status, _, checks)] = self._builds()
        self.assertEqual(status, "published")
        self.assertNotIn("charts", checks)
        self.assertFalse(any(line.startswith("Charts (") for line in lines))
        self.assertEqual(self._chart_rows(), {})

    def test_2_fallback_publishes_the_charts_as_the_pages_draw_them(self):
        # The pages' dates are all published, so a run of the charts alone fills in their history.
        code, lines = self._charts_run(pages=["charts"])
        self.assertEqual(code, 0, lines)
        build_id, status, reason, checks = self._builds()[-1]
        self.assertEqual((status, reason), ("published", None), checks)
        self.assertEqual(checks["charts"]["mode"], "fallback")
        self.assertEqual((checks["charts"]["reader_errors"], checks["charts"].get("left_out")), (0, {}), checks)
        self.assertEqual(checks["new"], checks["charts"]["written"])          # only chart rows are new
        self.assertIn(f"Charts (fallback): {checks['charts']['rows_read']} rows read", "\n".join(lines))
        stored = self._chart_rows()
        days = base._business_days(FIRST, LAST)
        self.assertEqual({key[5] for key in stored}, set(days))
        self.assertEqual({key[1] for key in stored}, set(charts.FIGURES))

        web, user = self.web, build.BUILD_USER

        def value(figure_id, as_of, item="", benchmark_id=""):
            row = stored[(SUBJECT, figure_id, "", item, benchmark_id, as_of)]
            return row["value"]

        # A series is one row per date, the point on that date; the chart through the last date is them.
        analytics = web.portfolio_analytics_view(SUBJECT, as_of=LAST.isoformat(), user=user)
        for point in analytics["rolling"]:
            self.assertEqual(value(charts.ROLLING_VOLATILITY, point["date"]), point["volatility"], point["date"])
        comparison = web.benchmark_comparison(SUBJECT, as_of=LAST.isoformat(), user=user)
        self.assertTrue(comparison["rolling"])
        for point in comparison["rolling"]:
            self.assertEqual(value(charts.ROLLING_BETA, point["date"], benchmark_id="SYNIDX"), point["beta"])
            self.assertEqual(value(charts.ROLLING_CORRELATION, point["date"], benchmark_id="SYNIDX"),
                             point["correlation"])
        series = comparison["series"]
        for previous, point in zip(series, series[1:]):
            for figure_id, name in ((charts.DAILY_PORTFOLIO, "portfolio"), (charts.DAILY_BENCHMARK, "benchmark")):
                self.assertTrue(math.isclose(value(figure_id, point["date"], benchmark_id="SYNIDX"),
                                             point[name] / previous[name] - 1, rel_tol=1e-9, abs_tol=1e-12))
        self.assertIsNone(value(charts.DAILY_PORTFOLIO, series[0]["date"], benchmark_id="SYNIDX"))
        # The tables are per as-of date, as the page shows them on that date.
        for as_of in (LAST.isoformat(), "2026-08-31", "2026-08-03"):
            with self.subTest(as_of=as_of):
                analytics = web.portfolio_analytics_view(SUBJECT, as_of=as_of, user=user)
                episodes = analytics["drawdown"]["episodes"]
                self.assertEqual(sorted(key[3] for key in stored if key[1] == charts.EPISODES and key[5] == as_of),
                                 sorted(e["peak_date"] for e in episodes))
                for rank, episode in enumerate(episodes, 1):
                    row = stored[(SUBJECT, charts.EPISODES, "", episode["peak_date"], "", as_of)]
                    self.assertEqual(row["value"], episode["depth"])
                holdings = web.holdings(SUBJECT, as_of=as_of, user=user)
                for holding in holdings["holdings"]:
                    self.assertEqual(value(charts.RSI, as_of, item=str(holding["conid"])), holding["rsi"])
                matrix = web.holdings_correlation_view(SUBJECT, as_of=as_of, user=user)
                self.assertTrue(matrix["available"])
                self.assertEqual(value(charts.HOLDINGS_CORRELATION, as_of, item="1001|1002"),
                                 matrix["matrix"][0][1]["correlation"])
        # Every chart row for every date: the readers on the website's own connections give the same rows.
        with build.own_connections(web):
            subject = build.subjects(web, [SUBJECT])[0][0]
            for as_of in days[::7]:
                fresh = build.page_rows(web, subject, as_of, ["charts"])
                old = {key: row for key, row in stored.items() if key[5] == as_of}
                self.assertEqual(sorted(row.key for row in fresh), sorted(old), as_of)
                for row in fresh:
                    self.assertEqual(row.compared(), tuple(build._canonical(old[row.key][name]) for name in build.COMPARED))

    def test_3_a_second_run_writes_nothing(self):
        count = len(self._builds())
        code, lines = self._charts_run()
        self.assertEqual(code, 0)
        self.assertIn("Nothing changed, so no build is written.", lines)
        self.assertEqual(len(self._builds()), count)

    def test_4_the_website_compares_in_shadow_and_shows_equal_points_when_on(self):
        web, user, as_of = self.web, build.BUILD_USER, LAST.isoformat()
        calls = {"analytics": lambda: web.portfolio_analytics_view(SUBJECT, as_of=as_of, user=user),
                 "benchmark-comparison": lambda: web.benchmark_comparison(SUBJECT, as_of=as_of, user=user),
                 "holdings": lambda: web.holdings(SUBJECT, as_of=as_of, user=user),
                 "holdings-correlation": lambda: web.holdings_correlation_view(SUBJECT, as_of=as_of, user=user)}
        today = {endpoint: call() for endpoint, call in calls.items()}
        with patch.dict(os.environ, {cf.PAGES_SETTING: "charts=shadow"}):
            for endpoint, call in calls.items():
                with self.subTest(endpoint=endpoint), self.assertLogs(LOGGER, level="INFO") as captured:
                    self.assertEqual(call(), today[endpoint])
                [line] = self._lines(captured)
                self.assertEqual((line["endpoint"], line["mode"], line["status"], line["subject"]),
                                 (endpoint, "shadow", "match", "u…01"), line)
                self.assertNotIn(SUBJECT, captured.output[0])
        with patch.dict(os.environ, {cf.PAGES_SETTING: "charts=on,performance=on,risk-statistics=on"}):
            for endpoint, call in calls.items():
                with self.subTest(endpoint=endpoint), self.assertNoLogs(LOGGER, level="INFO"):
                    shown = call()
                block = shown["certified_charts"]
                extract, figures = charts.ENDPOINTS[endpoint]
                self.assertEqual(block["figure_ids"], [f for f in figures if extract(today[endpoint]).figures.get(f)])
                self.assertEqual((block["as_of"], block["pages"]), (as_of, ["charts"]))
                self.assertEqual(charts.ENDPOINTS[endpoint][0](shown).figures.keys(),
                                 extract(today[endpoint]).figures.keys())
        # An older date shows that date's certified points.
        with patch.dict(os.environ, {cf.PAGES_SETTING: "charts=on"}):
            shown = web.holdings_correlation_view(SUBJECT, as_of="2026-08-31", user=user)
        self.assertEqual(shown["certified_charts"]["as_of"], "2026-08-31")

    def test_5_a_point_that_differs_is_todays_with_one_line(self):
        as_of = LAST.isoformat()
        stored = self._published()[(SUBJECT, charts.ROLLING_VOLATILITY, "", "", "", as_of)]
        build_id = self._publish_changed(charts.ROLLING_VOLATILITY, as_of, stored["value"] + 0.01)
        try:
            today = self.web.portfolio_analytics_view(SUBJECT, as_of=as_of, user=build.BUILD_USER)
            with patch.dict(os.environ, {cf.PAGES_SETTING: "charts=on,risk-statistics=on"}), \
                    self.assertLogs(LOGGER, level="INFO") as captured:
                shown = self.web.portfolio_analytics_view(SUBJECT, as_of=as_of, user=build.BUILD_USER)
            [line] = self._lines(captured)
            self.assertEqual((line["status"], line["today"], line["differ"]),
                             ("fallback", [charts.ROLLING_VOLATILITY], {charts.ROLLING_VOLATILITY: 1}))
            self.assertNotIn(str(round(stored["value"] + 0.01, 4))[2:], captured.output[0])
            self.assertEqual([p["volatility"] for p in shown["rolling"]], [p["volatility"] for p in today["rolling"]])
            self.assertEqual(shown["certified_charts"]["figure_ids"], [charts.EPISODES, charts.TRAILING])
        finally:
            with layers._connect(self.database) as owner:
                owner.execute("SELECT set_config('app.tenant_id', %s, false)", (base.TENANT,))
                owner.execute("UPDATE gold.certified_builds SET status = 'rolled_back', reason = 'test' "
                              "WHERE build_id = %s", (build_id,))
                owner.commit()

    def _publish_changed(self, figure_id: str, as_of: str, value: float) -> str:
        """A published build that changes one stored point (as a bad build would)."""
        import uuid

        build_id = str(uuid.uuid4())
        with layers._connect(self.database) as owner:
            owner.execute("SELECT set_config('app.tenant_id', %s, false)", (base.TENANT,))
            owner.execute("INSERT INTO gold.certified_builds(tenant_id, build_id, run_kind, started_at, status) "
                          "VALUES (%s, %s, 'daily', now(), 'shadow')", (base.TENANT, build_id))
            owner.execute(
                "INSERT INTO gold.certified_figures(tenant_id, build_id, subject_id, figure_id, period, item, "
                "benchmark_id, as_of_date, value, value_text, unit, currency, status, reason, detail, period_start, "
                "period_end, observations, definition_id, definition_version, calculation_version, input_sha256, "
                "source_table, source_key) SELECT tenant_id, %s, subject_id, figure_id, period, item, benchmark_id, "
                "as_of_date, %s, value_text, unit, currency, status, reason, detail, period_start, period_end, "
                "observations, definition_id, definition_version, calculation_version, input_sha256, source_table, "
                "source_key FROM gold.v_certified_figures WHERE subject_id = %s AND figure_id = %s AND as_of_date = %s",
                (build_id, value, SUBJECT, figure_id, as_of))
            owner.execute("UPDATE gold.certified_builds SET status = 'published', published_at = now() "
                          "WHERE build_id = %s", (build_id,))
            owner.commit()
        return build_id

    def test_6_the_rehearsal_reads_and_writes_nothing(self):
        def counts():
            return self._owner("SELECT (SELECT count(*) FROM gold.certified_builds), "
                               "(SELECT count(*) FROM gold.certified_figures)")[0]

        before = counts()
        lines: list[str] = []
        found = charts.rehearse(self.web_url, web=self.web, dates=0, out=lines.append)
        self.assertEqual(counts(), before)
        days = base._business_days(FIRST, LAST)
        self.assertEqual((found["subjects"], found["subject_dates_read"]), (1, len(days)))
        self.assertEqual((found["new"], found["changed"], found["same_inputs_new_answer"]), (0, 0, 0), found)
        self.assertEqual(found["unchanged"], found["rows"])
        self.assertEqual((found["rule_failures"], found["page_disagreements"], found["reader_errors"]), ({}, {}, {}))
        self.assertEqual(found["published_chart_rows"], found["rows"])
        self.assertAlmostEqual(found["full_history_rows"], found["rows"], delta=len(days) * 0.05)
        website = found["website"]["u…01"]
        self.assertEqual({figure for figure in website}, set(charts.FIGURES) | set(charts.BORROWED))
        self.assertTrue(all(result["shows"] == "certified" for result in website.values()), website)
        text = "\n".join(lines)
        self.assertIn("Rule failures: 0.", text)
        self.assertIn("Page disagreements (second read, the website's own connections): 0 subjects.", text)
        self.assertIn("Reader errors: 0.", text)
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn("1001", text)
        # The script: read-only on the web login, counts only.
        script = layers._script("check_certified_charts")
        with patch.dict(os.environ, {"DATABASE_URL": self.web_url}), \
                patch.object(charts.build, "load_web", lambda: self.web), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(script.main(["--dates", "2"]), 0)
        self.assertIn("2 of 2 subject dates read", printed.getvalue())
        self.assertEqual(counts(), before)

    def test_7_reader_errors_are_counted_in_fallback_and_hold_the_build_when_on(self):
        def broken(pages):
            raise ValueError("synthetic")

        readers = (*charts.READERS["charts"], broken)
        with patch.dict(charts.READERS, {"charts": readers}):
            count = len(self._builds())
            code, lines = self._charts_run("fallback")
            self.assertEqual(code, 0)
            self.assertIn("Nothing changed, so no build is written.", lines)
            self.assertTrue(any(line.startswith("Charts (fallback):") and line.endswith("5 reader errors.")
                                for line in lines), lines)
            self.assertEqual(len(self._builds()), count)
            code, lines = self._charts_run("on")
        self.assertEqual(code, 0)
        _, status, reason, checks = self._builds()[-1]
        self.assertEqual(status, "held")
        self.assertIn("could not be read", reason)
        self.assertEqual(checks["charts"]["mode"], "on")


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class ChartsHistoryTests(_ChartsDatabase):
    """With the pages published, a run of the charts alone fills in their history over several runs."""

    def test_the_charts_history_is_filled_in_newest_first(self):
        code, lines = self._run()
        self.assertEqual(code, 0, lines)
        self.assertEqual(self._chart_rows(), {})
        days = base._business_days(FIRST, LAST)
        code, lines = self._run(pages=["charts"], minutes=8.5 / 60, clock=base._Clock())
        self.assertEqual(code, 0, lines)
        self.assertIn(f"Read 8 of {len(days)} subject dates.", " ".join(lines))
        self.assertEqual(sorted({key[5] for key in self._chart_rows()}), days[-8:])
        [_, (_, status, _, checks)] = self._builds()
        self.assertEqual((status, checks["charts"]["mode"]), ("published", "fallback"))
        self.assertEqual(checks["figures_read"], checks["charts"]["rows_read"])   # the charts' rows only
        code, lines = self._run(pages=["charts"], minutes=8.5 / 60, clock=base._Clock())
        self.assertEqual(sorted({key[5] for key in self._chart_rows()}), days[-11:])
        code, lines = self._run(pages=["charts"])
        self.assertIn(f"Read {len(days) - 6} of {len(days) - 6} subject dates.", lines)
        self.assertEqual(sorted({key[5] for key in self._chart_rows()}), days)
        self.assertEqual([status for _, status, _, _ in self._builds()], ["published"] * 4)


if __name__ == "__main__":
    unittest.main()
