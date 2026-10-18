"""The charts, tables and indicators on certified figures (U5, after month-end item 4).

Synthetic data only. The answers are made with today's functions (portfolio_analytics,
benchmark_analytics, holding_rsi, holdings_correlation); the certified view is an in-memory
stand-in that answers certified_charts.READ_SQL. tests/test_certified_charts_db.py runs the
build's readers, the website and the rehearsal on PostgreSQL.
"""
from __future__ import annotations

import copy
import json
import math
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from ibkr_etl import certified_build as build
from ibkr_etl import certified_charts as charts
from ibkr_etl import certified_figures as cf
from ibkr_etl import certified_pages
from ibkr_etl.benchmark_analytics import ComparisonRow, comparison_metrics
from ibkr_etl.portfolio_analytics import (holding_rsi, holdings_correlation, portfolio_analytics,
                                          rolling_benchmark_statistics)

TENANT = "00000000-0000-0000-0000-000000000001"
SUBJECT = "U9300001"
SETTING = cf.PAGES_SETTING
LOGGER = "uvicorn.error.certified_figures"
PUBLISHED = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)


def _weekdays(start: str, count: int) -> list[str]:
    day, found = date.fromisoformat(start), []
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day.isoformat())
        day += timedelta(days=1)
    return found


DAYS = _weekdays("2025-06-02", 200)
LATEST = DAYS[-1]


def _analytics(day=LATEST):
    nav = [(d, f"{1_000_000 * (1 + 0.0005 * i + 0.04 * math.sin(i / 9)):.2f}") for i, d in enumerate(DAYS) if d <= day]
    return {"account_id": SUBJECT, "currency": "USD", "requested_as_of": day, "flows_applicable": True,
            **portfolio_analytics(nav, {}, as_of=day)}


def _benchmark(day=LATEST):
    rows, portfolio, index = [], 100.0, 100.0
    for i, d in enumerate(DAYS):
        if d > day:
            break
        p, b = (None, None) if i == 0 else (0.002 * math.sin(i / 3), 0.0015 * math.sin(i / 3 + 0.4))
        if i:
            portfolio *= 1 + p
            index *= 1 + b
        rows.append(ComparisonRow(d, portfolio * 1000, index * 50, portfolio, index, p, b,
                                  None if p is None else p - b))
    return {"account_id": SUBJECT, "as_of": day, "available": True,
            "benchmark": {"benchmark_id": "SYNIDX", "symbol": "^SYN", "name": "Synthetic index"},
            "benchmark_return_convention": "price", "reporting_currency": "USD",
            "calculation_version": "benchmark-comparison-v1", "metrics": comparison_metrics(rows),
            "rolling": rolling_benchmark_statistics(rows),
            "series": [{"date": r.observation_date, "portfolio": r.normalized_portfolio,
                        "benchmark": r.normalized_benchmark} for r in rows]}


def _marks(conid: int):
    return {d: 100 + 5 * math.sin(i / (2 + conid % 5)) + conid % 7 for i, d in enumerate(DAYS)}


def _holdings(day=LATEST):
    rows = [{"conid": conid, "symbol": f"SYN{conid}", "weight": 0.1, **holding_rsi(_marks(conid), day)}
            for conid in (1001, 1002, 1003)]
    return {"account_id": SUBJECT, "snapshot_date": day, "valuation_method": "holdings-view-v1",
            "holdings": rows}


def _correlation(day=LATEST):
    labels = {str(conid): f"SYN{conid}" for conid in (1003, 1001, 1002)}
    prices = {conid: {d: v for d, v in _marks(int(conid)).items() if d <= day} for conid in labels}
    return {"account_id": SUBJECT, "snapshot_date": day, **holdings_correlation(prices, labels)}


ANSWERS = {"analytics": _analytics, "benchmark-comparison": _benchmark, "holdings": _holdings,
           "holdings-correlation": _correlation}


def certified_rows(endpoint: str, payload, *, figures=None) -> list[dict]:
    """The rows a build would have published for this answer: one per point, at the point's date."""
    extract, shown = charts.ENDPOINTS[endpoint]
    points = extract(payload)
    rows = []
    for figure_id, values in points.figures.items():
        if figures is not None and figure_id not in figures:
            continue
        per_benchmark = cf.figure(figure_id).per_benchmark
        for (day, item), (value, detail) in values.items():
            rows.append({"subject": SUBJECT, "figure_id": figure_id, "item": item,
                         "benchmark_id": points.benchmark_id if per_benchmark else "", "as_of_date": day,
                         "value": value, "unit": cf.figure(figure_id).unit,
                         "status": "ok" if value is not None else "unavailable", "detail": detail or {},
                         "build_id": "b1", "published_at": PUBLISHED})
    return rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeView:
    """Answers certified_charts.READ_SQL as the view does."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.reads = 0

    def execute(self, sql, params):
        assert sql == charts.READ_SQL
        self.reads += 1
        return _Result([dict(row) for row in self.rows
                        if row["subject"] == params["subject"] and row["figure_id"] in params["figures"]
                        and row["benchmark_id"] in params["benchmarks"]
                        and params["first"] <= row["as_of_date"] <= params["effective"]
                        and (row["figure_id"] in params["series"] or row["as_of_date"] == params["effective"])])

    @contextmanager
    def connect(self, tenant_id):
        assert tenant_id == TENANT
        yield self


def _forbidden(tenant_id):
    raise AssertionError("the certified view must not be read")


def _lines(captured) -> list[dict]:
    found = []
    for message in captured.output:
        prefix, _, body = message.split(":", 2)[2].partition(" ")
        assert prefix == certified_pages.LOG_PREFIX
        found.append(json.loads(body))
    return found


class ExtractionTests(unittest.TestCase):
    def test_analytics_points_are_the_charts_and_the_table(self):
        payload = _analytics()
        points = charts.analytics_points(payload)
        self.assertEqual(points.effective, LATEST)
        volatility = points.figures[charts.ROLLING_VOLATILITY]
        self.assertEqual(len(volatility), len(payload["rolling"]))
        last = payload["rolling"][-1]
        self.assertEqual(volatility[(LATEST, "")][0], last["volatility"])
        self.assertEqual(points.figures[charts.TRAILING][(LATEST, "")][0], last["trailing_return"])
        episodes = payload["drawdown"]["episodes"]
        self.assertTrue(episodes)
        table = points.figures[charts.EPISODES]
        self.assertEqual(len(table), len(episodes))
        first = episodes[0]
        value, detail = table[(LATEST, first["peak_date"])]
        self.assertEqual(value, first["depth"])
        self.assertEqual(detail["rank"], 1)
        self.assertEqual(detail["trough_date"], first["trough_date"])
        self.assertEqual(charts.analytics_points({"available": False}).figures, {})

    def test_a_series_point_is_the_same_whatever_date_the_chart_is_drawn_to(self):
        earlier = DAYS[150]
        now = charts.analytics_points(_analytics())
        then = charts.analytics_points(_analytics(earlier))
        for figure_id in (charts.ROLLING_VOLATILITY, charts.TRAILING):
            for key, value in then.figures[figure_id].items():
                self.assertEqual(now.figures[figure_id][key], value, (figure_id, key))
        now, then = charts.benchmark_points(_benchmark()), charts.benchmark_points(_benchmark(earlier))
        for figure_id in (charts.DAILY_PORTFOLIO, charts.DAILY_BENCHMARK, charts.ROLLING_BETA, charts.REBASED):
            for key, value in then.figures[figure_id].items():
                self.assertEqual(now.figures[figure_id][key], value, (figure_id, key))

    def test_the_daily_returns_are_the_changes_in_the_drawn_levels(self):
        payload = _benchmark()
        points = charts.benchmark_points(payload)
        self.assertEqual((points.effective, points.benchmark_id), (LATEST, "SYNIDX"))
        returns = points.figures[charts.DAILY_PORTFOLIO]
        self.assertNotIn((DAYS[0], ""), returns)                      # the first date has none
        self.assertEqual(len(returns), len(DAYS) - 1)
        for i in (1, 50, 199):
            expected = 0.002 * math.sin(i / 3)
            self.assertTrue(math.isclose(returns[(DAYS[i], "")][0], expected, rel_tol=1e-9, abs_tol=1e-12))
        self.assertEqual(points.figures[charts.REBASED][(LATEST, "portfolio")][0], payload["series"][-1]["portfolio"])
        rolling = points.figures[charts.ROLLING_BETA]
        self.assertEqual(len(rolling), len(DAYS) - 126)
        self.assertEqual(charts.benchmark_points({"available": False}).figures, {})

    def test_rsi_per_holding_and_none_for_composites(self):
        points = charts.holdings_points(_holdings())
        rsi = points.figures[charts.RSI]
        self.assertEqual(sorted(item for _, item in rsi), ["1001", "1002", "1003"])
        value, detail = rsi[(LATEST, "1001")]
        self.assertTrue(0 <= value <= 100)
        self.assertEqual(set(detail), {"signal", "observations", "reason", "restarted"})
        composite = {"snapshot_date": LATEST, "holdings": [{"conid": "SLEEVE", "market_value_text": "1"}]}
        self.assertEqual(charts.holdings_points(composite).figures, {})

    def test_each_pair_once_with_the_lower_conid_first(self):
        payload = _correlation()
        matrix = charts.correlation_points(payload).figures[charts.HOLDINGS_CORRELATION]
        self.assertEqual(sorted(item for _, item in matrix), ["1001|1002", "1001|1003", "1002|1003"])
        holdings = [h["conid"] for h in payload["holdings"]]
        i, j = holdings.index("1003"), holdings.index("1001")
        self.assertEqual(matrix[(LATEST, "1001|1003")][0], payload["matrix"][i][j]["correlation"])
        self.assertEqual(matrix[(LATEST, "1001|1003")][1], {"observations": payload["matrix"][i][j]["observations"]})


class SettingTests(unittest.TestCase):
    def test_build_mode(self):
        self.assertEqual(charts.build_mode({}), "off")
        for text, mode in (("", "off"), ("off", "off"), (" Fallback ", "fallback"), ("on", "on"), ("shadow", "off")):
            self.assertEqual(charts.build_mode({charts.SETTING: text}), mode, text)

    def test_the_build_reads_the_charts_only_when_asked(self):
        pages = list(build.READERS)
        self.assertEqual(charts.run_pages(None, build.READERS, {}), (pages, None))
        self.assertEqual(charts.run_pages(None, build.READERS, {charts.SETTING: "off"}), (pages, None))
        self.assertEqual(charts.run_pages(None, build.READERS, {charts.SETTING: "fallback"}),
                         (pages + ["charts"], "fallback"))
        self.assertEqual(charts.run_pages(["charts"], build.READERS, {}), (["charts"], "fallback"))
        self.assertEqual(charts.run_pages(["charts"], build.READERS, {charts.SETTING: "on"}), (["charts"], "on"))
        self.assertEqual(charts.run_pages(["overview"], build.READERS, {charts.SETTING: "fallback"}),
                         (["overview"], None))

    def test_the_register_and_the_module_agree(self):
        self.assertEqual({f.id for f in cf.figures().values() if f.page == "charts"}, set(charts.FIGURES))
        self.assertEqual(cf.page("charts").batch, 4)
        self.assertEqual(set(cf.page("charts").shows_from_other_pages), set(charts.BORROWED))
        for figure_id, owner in charts.BORROWED.items():
            self.assertEqual(cf.figure(figure_id).page, owner)
        for figure_id in charts.SERIES:
            self.assertEqual(cf.figure(figure_id).kind, "series")
        for figure_id in charts.TABLES:
            self.assertEqual(cf.figure(figure_id).kind, "per_item")
        for endpoint, (_, shown) in charts.ENDPOINTS.items():
            self.assertIn(endpoint, {"analytics", "benchmark-comparison", "holdings", "holdings-correlation"})
            self.assertTrue(set(shown) <= charts.FIGURES | set(charts.BORROWED))

    def test_the_rules_cover_the_chart_figures(self):
        def row(figure_id, value):
            return build.Row(subject_id=SUBJECT, figure_id=figure_id, as_of_date=LATEST, source_table="x",
                             source_key={}, input_sha256="0" * 64, calculation_version="v", value=value)

        self.assertEqual(build.rule_failures([row(charts.EPISODES, 0.01), row(charts.EPISODES, -0.2),
                                              row(charts.HOLDINGS_CORRELATION, 1.2),
                                              row(charts.ROLLING_CORRELATION, -1.0), row(charts.RSI, 101.0),
                                              row(charts.RSI, 0.0), row(charts.ROLLING_VOLATILITY, -0.01),
                                              row(charts.ROLLING_BETA, 3.0)]),
                         [charts.EPISODES, charts.HOLDINGS_CORRELATION, charts.RSI, charts.ROLLING_VOLATILITY])


class ServeTests(unittest.TestCase):
    def serve(self, endpoint, setting, rows, payload=None, *, connect=None):
        payload = ANSWERS[endpoint]() if payload is None else payload
        view = FakeView(rows)
        with patch.object(charts, "_connect", connect or view.connect):
            shown = charts.serve(endpoint, payload, tenant_id=TENANT, subject_id=SUBJECT,
                                 environ={SETTING: setting})
        return shown, view

    def test_off_returns_the_answer_itself_with_no_read_and_no_log(self):
        for setting in ("", "off", "overview=on,holdings=on", "charts=off", "charts=maybe"):
            for endpoint, make in ANSWERS.items():
                payload = make()
                before = copy.deepcopy(payload)
                with self.subTest(setting=setting, endpoint=endpoint), patch.object(charts, "_connect", _forbidden), \
                        self.assertNoLogs(LOGGER, level="INFO"):
                    shown = charts.serve(endpoint, payload, tenant_id=TENANT, subject_id=SUBJECT,
                                         environ={SETTING: setting})
                self.assertIs(shown, payload)
                self.assertEqual(shown, before)
        with patch.object(charts, "_connect", _forbidden), self.assertNoLogs(LOGGER, level="INFO"):
            self.assertIsNone(charts.serve("summary", None, tenant_id=TENANT, subject_id=SUBJECT,
                                           environ={SETTING: "charts=on"}))

    def test_shadow_shows_today_and_logs_a_match(self):
        for endpoint, make in ANSWERS.items():
            payload = make()
            before = copy.deepcopy(payload)
            with self.subTest(endpoint=endpoint), self.assertLogs(LOGGER, level="INFO") as captured:
                shown, _ = self.serve(endpoint, "charts=shadow", certified_rows(endpoint, payload), payload)
            self.assertEqual(shown, before)
            [line] = _lines(captured)
            self.assertEqual((line["page"], line["mode"], line["status"], line["subject"]),
                             ("charts", "shadow", "match", "u…01"))
            self.assertGreater(line["compared"], 0)
            self.assertEqual((line["differ"], line["missing"], line["extra"]), ({}, {}, {}))

    def test_shadow_counts_differences_by_figure_id_only(self):
        payload = _analytics()
        rows = certified_rows("analytics", payload)
        secret = 0.123456789
        changed = next(row for row in rows if row["figure_id"] == charts.ROLLING_VOLATILITY and row["value"])
        changed["value"] = secret
        rows = [row for row in rows if not (row["figure_id"] == charts.EPISODES and row["detail"]["rank"] == 2)]
        rows.append({**next(row for row in rows if row["figure_id"] == charts.EPISODES), "item": "2020-01-01"})
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.serve("analytics", "charts=shadow", rows, payload)
        [line] = _lines(captured)
        self.assertEqual(line["status"], "differ")
        self.assertEqual(line["differ"], {charts.ROLLING_VOLATILITY: 1})
        self.assertEqual(line["missing"], {charts.EPISODES: 1})
        self.assertEqual(line["extra"], {charts.EPISODES: 1})
        text = captured.output[0]
        self.assertNotIn(SUBJECT, text)
        self.assertNotIn(str(secret)[:6], text)
        self.assertNotIn("2020-01-01", text)

    def test_shadow_with_nothing_built_says_none(self):
        with self.assertLogs(LOGGER, level="INFO") as captured:
            self.serve("holdings", "charts=shadow", [])
        [line] = _lines(captured)
        self.assertEqual((line["status"], line["missing"]), ("none", {charts.RSI: 3}))

    def test_on_shows_certified_points_that_equal_todays_with_a_block(self):
        for endpoint, make in ANSWERS.items():
            payload = make()
            rows = certified_rows(endpoint, payload)
            for row in rows:   # within the tolerance: one part in ten billion
                if row["value"] is not None:
                    row["value"] *= 1 + 1e-10
            with self.subTest(endpoint=endpoint), self.assertNoLogs(LOGGER, level="INFO"):
                shown, _ = self.serve(endpoint, "charts=on,performance=on,risk-statistics=on", rows, payload)
            block = shown["certified_charts"]
            extract, figures = charts.ENDPOINTS[endpoint]
            today = extract(make())
            self.assertEqual(block["figure_ids"], [f for f in figures if today.figures.get(f)])
            self.assertEqual((block["as_of"], block["pages"], block["build_id"], block["freshness"]["status"]),
                             (today.effective, ["charts"], "b1", "fresh"))
            self.assertTrue(block["label"].startswith("Certified · as of "))
            self.assertEqual(set(shown) - {"certified_charts"}, set(make()))   # today's shape, one block added
        shown, _ = self.serve("analytics", "charts=on,risk-statistics=on", [
            {**row, "value": row["value"] * (1 + 1e-10) if row["value"] is not None else None}
            for row in certified_rows("analytics", _analytics())])
        point = shown["rolling"][-1]
        self.assertNotEqual(point["volatility"], _analytics()["rolling"][-1]["volatility"])   # the certified value
        self.assertTrue(math.isclose(point["volatility"], _analytics()["rolling"][-1]["volatility"], rel_tol=1e-9))

    def test_on_falls_back_to_today_for_a_figure_that_differs_with_one_line(self):
        payload = _benchmark()
        today = copy.deepcopy(payload)
        rows = certified_rows("benchmark-comparison", payload)
        beta = next(row for row in rows if row["figure_id"] == charts.ROLLING_BETA and row["value"] is not None)
        beta["value"] += 0.01
        with self.assertLogs(LOGGER, level="INFO") as captured:
            shown, _ = self.serve("benchmark-comparison", "charts=on,performance=on", rows, payload)
        [line] = _lines(captured)
        self.assertEqual((line["mode"], line["status"], line["today"], line["differ"]),
                         ("on", "fallback", [charts.ROLLING_BETA], {charts.ROLLING_BETA: 1}))
        self.assertIn(charts.DAILY_PORTFOLIO, line["used"])
        self.assertEqual([p["beta"] for p in shown["rolling"]], [p["beta"] for p in today["rolling"]])
        self.assertNotIn(charts.ROLLING_BETA, shown["certified_charts"]["figure_ids"])
        self.assertIn(charts.ROLLING_CORRELATION, shown["certified_charts"]["figure_ids"])

    def test_on_missing_or_behind_is_todays(self):
        payload = _analytics()
        rows = [row for row in certified_rows("analytics", payload) if row["as_of_date"] < LATEST]
        with self.assertLogs(LOGGER, level="INFO") as captured:
            shown, _ = self.serve("analytics", "charts=on,risk-statistics=on", rows, payload)
        self.assertNotIn("certified_charts", shown)
        self.assertEqual(shown, _analytics())
        [line] = _lines(captured)
        self.assertEqual(line["status"], "fallback")
        self.assertEqual(line["missing"], {charts.ROLLING_VOLATILITY: 1, charts.EPISODES: len(payload["drawdown"]["episodes"]),
                                           charts.TRAILING: 1})

    def test_a_borrowed_series_waits_for_its_own_page(self):
        payload = _analytics()
        rows = certified_rows("analytics", payload)
        with self.assertNoLogs(LOGGER, level="INFO"):
            shown, view = self.serve("analytics", "charts=on,risk-statistics=shadow", rows, payload)
        self.assertNotIn(charts.TRAILING, shown["certified_charts"]["figure_ids"])
        self.assertIn(charts.ROLLING_VOLATILITY, shown["certified_charts"]["figure_ids"])
        shown, _ = self.serve("analytics", "charts=on,risk-statistics=on", rows, _analytics())
        self.assertIn(charts.TRAILING, shown["certified_charts"]["figure_ids"])

    def test_a_failing_read_is_todays_with_an_error_line(self):
        def broken(tenant_id):
            raise RuntimeError("the view is missing")

        for setting in ("charts=shadow", "charts=on"):
            payload = _holdings()
            with self.subTest(setting=setting), self.assertLogs(LOGGER, level="INFO") as captured:
                shown, _ = self.serve("holdings", setting, [], payload, connect=broken)
            self.assertEqual(shown, _holdings())
            [line] = _lines(captured)
            self.assertEqual((line["status"], line["error"]), ("error", "RuntimeError"))

    def test_an_unavailable_answer_reads_nothing(self):
        with patch.object(charts, "_connect", _forbidden), self.assertNoLogs(LOGGER, level="INFO"):
            for endpoint in ANSWERS:
                payload = {"available": False, "message": "No data."}
                self.assertIs(charts.serve(endpoint, payload, tenant_id=TENANT, subject_id=SUBJECT,
                                           environ={SETTING: "charts=on"}), payload)

    def test_the_matrix_is_written_both_ways(self):
        payload = _correlation()
        rows = certified_rows("holdings-correlation", payload)
        for row in rows:
            row["value"] = row["value"] * (1 + 1e-10)
        shown, _ = self.serve("holdings-correlation", "charts=on", rows, payload)
        matrix = shown["matrix"]
        for i in range(3):
            self.assertEqual(matrix[i][i]["correlation"], 1.0)
            for j in range(3):
                self.assertEqual(matrix[i][j]["correlation"], matrix[j][i]["correlation"])


class SettleTests(unittest.TestCase):
    def rows(self):
        def row(figure_id, value, day=LATEST, subject=SUBJECT):
            return build.Row(subject_id=subject, figure_id=figure_id, as_of_date=day, source_table="x",
                             source_key={}, input_sha256="0" * 64, calculation_version="v", value=value)

        return {"good": row(charts.ROLLING_VOLATILITY, 0.1), "broken": row(charts.RSI, 120.0),
                "same": row(charts.ROLLING_BETA, 1.1), "moved": row(charts.HOLDINGS_CORRELATION, 0.3),
                "page": row("stats.sortino_ratio", 1.0), "page_same": row("stats.calmar_ratio", 1.0)}

    def test_fallback_leaves_chart_problems_out_and_never_holds(self):
        rows = self.rows()
        result = {"new": [rows["good"], rows["broken"], rows["moved"], rows["page"]],
                  "changed": [rows["same"], rows["page_same"]], "unchanged": 0,
                  "violations": [rows["same"], rows["page_same"]]}
        disagreements = {"u…01": [charts.HOLDINGS_CORRELATION], "u…02": ["overview.nav", charts.RSI]}
        notes = {"chart_errors": {"rsi_rows": {"KeyError": 2}}, "page_unavailable": {}}
        errors: list = []
        counts = charts.settle("fallback", list(rows.values()), result, disagreements, notes, errors)
        self.assertEqual(result["new"], [rows["good"], rows["page"]])
        self.assertEqual(result["changed"], [rows["page_same"]])
        self.assertEqual(result["violations"], [rows["page_same"]])       # a page's own still holds the build
        self.assertEqual(disagreements, {"u…02": ["overview.nav"]})
        self.assertEqual(errors, [])
        self.assertNotIn("chart_errors", notes)
        self.assertEqual(counts, {"mode": "fallback", "rows_read": 4, "reader_errors": 2, "written": 1,
                                  "left_out": {"page_disagreements": 1, "rule_failures": 1, "same_inputs_new_answer": 1},
                                  "left_out_figures": sorted([charts.RSI, charts.ROLLING_BETA, charts.HOLDINGS_CORRELATION]),
                                  "disagreeing_subjects": 2})
        self.assertIn("3 left out", charts.summary_line(counts))

    def test_on_lets_the_builds_own_checks_decide(self):
        rows = self.rows()
        result = {"new": [rows["good"], rows["broken"]], "changed": [rows["same"]], "unchanged": 0,
                  "violations": [rows["same"]]}
        disagreements = {"u…01": [charts.HOLDINGS_CORRELATION]}
        errors: list = []
        counts = charts.settle("on", list(rows.values()), result, disagreements,
                               {"chart_errors": {"rsi_rows": {"KeyError": 2}}}, errors)
        self.assertEqual(result["violations"], [rows["same"]])
        self.assertEqual(disagreements, {"u…01": [charts.HOLDINGS_CORRELATION]})
        self.assertEqual(errors, ["charts rsi_rows: KeyError x2"])
        self.assertEqual((counts["mode"], counts["written"], counts["reader_errors"]), ("on", 3, 2))


class PageRowsTests(unittest.TestCase):
    """A chart reader's error is counted, not raised, when the run counts; the pages' own still raise."""

    def test_chart_errors_are_counted(self):
        subject = build.Subject(SUBJECT, {"reporting_currency": "USD"}, [LATEST])

        def boom(pages):
            raise KeyError("x")

        with patch.dict(charts.READERS, {"charts": (boom,)}):
            notes: dict = {}
            self.assertEqual(build.page_rows(object(), subject, LATEST, ["charts"], notes=notes), [])
            self.assertEqual(notes, {"chart_errors": {"boom": {"KeyError": 1}}})
            with self.assertRaises(KeyError):
                build.page_rows(object(), subject, LATEST, ["charts"])
        with patch.dict(build.READERS, {"cash": (boom,)}), self.assertRaises(KeyError):
            build.page_rows(object(), subject, LATEST, ["cash"], notes={})


class ReleaseFiguresTests(unittest.TestCase):
    """The release data report's figures command compares the charts' points on each date."""

    def compare(self, change=None):
        import sys
        from pathlib import Path

        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import release_data_figures as figures
        from test_release_data_figures import FakeConnection, FakePages

        answers = {"analytics": _analytics(), "benchmark": _benchmark(), "holdings": _holdings(),
                   "holdings_correlation": _correlation()}
        answers = json.loads(json.dumps(answers, default=str))
        rows = []
        for endpoint, name in (("analytics", "analytics"), ("benchmark-comparison", "benchmark"),
                               ("holdings", "holdings"), ("holdings-correlation", "holdings_correlation")):
            for row in certified_rows(endpoint, answers[name]):
                if row["figure_id"] in charts.FIGURES:
                    rows.append({"subject_id": SUBJECT, "figure_id": row["figure_id"], "period": "",
                                 "item": row["item"], "benchmark_id": row["benchmark_id"],
                                 "as_of_date": row["as_of_date"], "value": row["value"], "value_text": None,
                                 "unit": row["unit"], "status": row["status"]})
        if change:
            change(rows)
        comparison = figures.compare(["charts"], FakePages(answers), FakeConnection(rows), {SUBJECT: [LATEST]})
        return figures.build_report(comparison, source="test")

    def test_the_points_on_the_date_match(self):
        report = self.compare()
        self.assertEqual(report["differences"], [])
        self.assertEqual(set(report["figures"]), set(charts.FIGURES))
        self.assertGreater(report["pages"]["charts"]["matched"], 10)

    def test_a_point_that_differs_is_a_difference(self):
        def change(rows):
            next(row for row in rows if row["figure_id"] == charts.RSI and row["as_of_date"] == LATEST)["value"] += 1

        report = self.compare(change)
        self.assertEqual([(d["figure_id"], d["kind"]) for d in report["differences"]], [(charts.RSI, "different")])


class ScriptTests(unittest.TestCase):
    def test_the_line_picks_the_figures_it_names(self):
        from ibkr_etl.web_ui import CERTIFIED_LINE_SCRIPT

        self.assertIn("const chartsFor=", CERTIFIED_LINE_SCRIPT)
        # The two-figure list lives here, not in the analytics script, whose ['…', lists the Metrics guide
        # audit reads as card labels.
        self.assertIn("const rollingBenchmarkCharts=['series.rolling_beta_126d','series.rolling_correlation_126d'];",
                      CERTIFIED_LINE_SCRIPT)

    def test_every_chart_card_draws_its_line(self):
        from ibkr_etl import web
        from ibkr_etl.web_analytics_ui import ANALYTICS_SCRIPT

        for marker in ("chartsFor(charts,['table.drawdown_episodes'],'Deepest drawdowns')",
                       "chartsFor(charts,['series.rolling_volatility_63d'])",
                       "chartsFor(charts,['stats.trailing_12m_return'])",
                       "chartsFor(bc&&bc.certified_charts,rollingBenchmarkCharts)",
                       "chartsFor(r.certified_charts,['matrix.holdings_correlation'])"):
            self.assertIn(marker, ANALYTICS_SCRIPT)
        owner = {"email": "owner@example.com", "role": "owner"}
        account = web._render_account_page(owner, account_id=web.PILOT_ACCOUNT_ID, section="performance")
        self.assertIn("chartsFor(bc.certified_charts,['series.portfolio_daily_return','series.benchmark_daily_return'],"
                      "'Vs benchmark')", account)
        self.assertIn("const rollingBenchmarkCharts=", account)


if __name__ == "__main__":
    unittest.main()
