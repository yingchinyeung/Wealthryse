"""The agents' figure tools on certified figures with the automatic fallback, the month-end job's
read tools and the enforced [F#] check (certified figures, "After month-end" item 3).

No database or network: the tools run over patched inputs and a fake certified reader; synthetic
ids only. tests/test_agent_certified_fallback_db.py runs the same paths on PostgreSQL.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import os
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from ibkr_etl import agent_tools as web_tools  # noqa: E402
from ibkr_etl import ai_analyst, reporting_snapshot  # noqa: E402
from ibkr_etl import certified_figures as register  # noqa: E402
from ibkr_etl import certified_tool_figures as core  # noqa: E402
from ibkr_etl.certified_tool_figures import Slot  # noqa: E402
from wealthryse_agent import certified_fallback, certified_tools, jobs, tool_server  # noqa: E402
from wealthryse_agent import tools as t  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.prompts import MONTH_END_CERTIFIED, MONTH_END_DRAFTER  # noqa: E402
from wealthryse_agent.runtime import Agent, UNVERIFIED, figure_citations, number_attached  # noqa: E402
from wealthryse_agent.validator import validate_answer  # noqa: E402

ACCOUNT = "U9300001"
MASKED = "u…01"
BUILD = "5d0c0000-0000-4000-8000-0000000000aa"
SETTINGS = (core.SETTING, certified_tools.AGENT_SETTING, certified_tools.PAGES_SETTING,
            certified_tools.MONTH_END_SETTING, certified_tools.CHECK_SETTING)
SERVED = {certified_tools.AGENT_SETTING: "on",
          certified_tools.PAGES_SETTING: "overview=on,holdings=on,risk-statistics=on,risk-snapshot=on,performance=on"}
CERTIFIED = {certified_tools.GET, certified_tools.TRACE}


def _env(**values):
    """A patch of os.environ with every setting here cleared, then ``values`` set."""
    patcher = mock.patch.dict(os.environ, {})
    patcher.start()
    for name in SETTINGS:
        os.environ.pop(name, None)
    os.environ.update(values)
    return patcher


def _row(figure_id, as_of, value, period="", item="", benchmark_id="", status="ok"):
    figure = register.figure(figure_id)
    text = figure.unit in ("date", "text")
    return {"figure_id": figure_id, "period": period, "item": item, "benchmark_id": benchmark_id,
            "as_of_date": date.fromisoformat(as_of), "value": None if text else value,
            "value_text": value if text else None, "unit": figure.unit,
            "currency": "USD" if figure.unit == "money" else None, "status": status,
            "definition_id": figure.definition_id, "definition_version": figure.definition_version,
            "calculation_version": "test-v1", "build_id": BUILD, "published_at": None}


class FakeReader:
    """Published certified rows, looked up as the real readers do; records each read."""

    def __init__(self, rows=(), fail=None):
        self.rows, self.fail, self.calls = list(rows), fail, []

    def __call__(self, figure_ids, dates, benchmark_ids):
        self.calls.append((list(figure_ids), list(dates), list(benchmark_ids)))
        if self.fail:
            raise self.fail
        return {core.key_of(r): r for r in self.rows if r["figure_id"] in figure_ids
                and r["as_of_date"].isoformat() in dates and r["benchmark_id"] in ("", *benchmark_ids)}


def _never(*_):
    raise AssertionError("the certified figures were read")


# --- Synthetic history -------------------------------------------------------------------------

DAYS = [(date(2024, 9, 30) + timedelta(days=i)).isoformat() for i in range(0, 553, 7)]  # over 18 months
NAV = {d: 100.0 * (1 + 0.002 * i + (0.01 if i % 2 else -0.01)) for i, d in enumerate(DAYS)}
SUBJECT = {"subject_id": ACCOUNT, "subject_type": "physical_account", "reporting_currency": "USD",
           "base_currency": "USD", "history_start_date": DAYS[0]}


def _patched_history(end=DAYS[-1], flows=None):
    nav = {d: v for d, v in NAV.items() if d <= end}
    flows = {} if flows is None else flows
    return [mock.patch.object(t, "_subject", return_value=SUBJECT),
            mock.patch.object(t, "_latest_nav_date", return_value=end),
            mock.patch.object(t, "_series_inputs", return_value=(nav, {})),
            mock.patch.object(t, "_nav_and_flows", return_value=([(d, str(v)) for d, v in nav.items()],
                                                                 None if flows == "none" else flows)),
            mock.patch.object(t, "_benchmark_for", return_value=None)]


def _run(tool, arguments, reader=None, flows=None):
    patches = _patched_history(flows=flows)
    if reader is not None:
        patches.append(mock.patch.object(certified_fallback, "reader", return_value=reader))
    for patch in patches:
        patch.start()
    try:
        return tool(None, {"account_id": ACCOUNT, **arguments})
    finally:
        for patch in reversed(patches):
            patch.stop()


class OffByDefaultTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_env().stop)

    def test_every_setting_is_off_unless_set(self):
        self.assertEqual(core.mode({}), "off")
        for value in ("", "off", "shadow", "yes", "true", "FALLBACK "):
            self.assertEqual(core.mode({core.SETTING: value}), "fallback" if "FALLBACK" in value else "off")
        self.assertEqual(core.mode({core.SETTING: "on"}), "on")
        self.assertEqual(certified_tools.figure_check({}), "warn")
        self.assertEqual(certified_tools.figure_check({certified_tools.CHECK_SETTING: "strict"}), "warn")
        self.assertEqual(certified_tools.figure_check({certified_tools.CHECK_SETTING: "enforce"}), "enforce")
        self.assertFalse(certified_tools.month_end_enabled({}))
        self.assertFalse(certified_tools.released(certified_tools.GET, "month_end_drafter", {}))

    def test_tools_read_nothing_and_answer_as_before(self):
        identity = mock.patch.object(certified_fallback, "apply", lambda connection, tool, account, result, *a, **k: result)
        for tool, arguments in ((t.get_risk_metrics, {"from_date": "2000-01-01"}), (t.get_risk_metrics, {}),
                                (t.get_performance, {}), (t.get_nav_history, {})):
            with mock.patch.object(certified_fallback, "reader", _never):
                result = _run(tool, arguments)
            with identity:
                before = _run(tool, arguments)
            self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(before, sort_keys=True), tool.__name__)
            self.assertNotIn("certified", result)
            self.assertNotIn(core.SOURCE_FIELD, result)

    def test_the_month_end_job_keeps_its_tools_and_task(self):
        seen = []
        with mock.patch.dict(os.environ, SERVED), mock.patch.object(
                jobs, "_run_agent", lambda agent, tools, account, task, allowed: seen.append((task, allowed)) or ("", [], {}, False)):
            store = SimpleNamespace(inbox_exists=lambda *a: False)
            jobs.month_end_drafter(store, None, None, [ACCOUNT], {}, today=date(2026, 11, 2))
        task, allowed = seen[0]
        self.assertEqual(allowed, ["get_performance", "compare_to_benchmark", "get_holdings_changes",
                                   "get_sector_allocation", "get_cash_flows"])
        self.assertEqual(task, MONTH_END_DRAFTER.format(account_id=ACCOUNT, month_end="2026-10-31",
                                                       month_start="2026-10-01"))
        with mock.patch.dict(os.environ, SERVED):
            self.assertFalse(CERTIFIED & {x["name"] for x in tool_server.tool_list("month_end_drafter")})

    def test_the_website_analyst_calls_its_tools_as_before(self):
        source = inspect.getsource(ai_analyst.generate_grounded_narrative)
        self.assertIn('execute_tool(connection, tool_context, tool_name, arguments) if certified == "off"', source)


class SettleTest(unittest.TestCase):
    """The fallback rule itself, on one answer with four figures."""

    def _answer(self):
        return {"data": {"latest_nav": 12705.0, "periods": [{"key": "ytd", "twr": 0.0512, "irr_annualized": 0.07},
                                                            {"key": "mtd", "twr": 0.0101}]}}

    def _slots(self, answer):
        data = answer["data"]
        return [Slot("overview.nav", "2026-03-31", data, "latest_nav", "/data/latest_nav"),
                Slot("overview.twr", "2026-03-31", data["periods"][0], "twr", "/data/periods/0/twr", period="ytd"),
                Slot("overview.irr_annualised", "2026-03-31", data["periods"][0], "irr_annualized",
                     "/data/periods/0/irr_annualized", period="ytd"),
                Slot("overview.twr", "2026-03-31", data["periods"][1], "twr", "/data/periods/1/twr", period="mtd")]

    def _settle(self, mode, reader):
        answer = self._answer()
        logger = logging.getLogger("test.certified_tool_figures")
        with self.assertLogs(logger, "INFO") as logged, core.observe() as counts:
            core.settle("get_performance", ACCOUNT, answer, self._slots(answer), reader, mode, logger)
            logger.info("end")
        return answer, [line for line in logged.output if core.LOG_PREFIX in line], counts

    ROWS = [_row("overview.nav", "2026-03-31", 12705.004),  # to the cent: equal
            _row("overview.twr", "2026-03-31", 0.0512 * (1 + 1e-12), period="ytd"),  # within 1e-9: equal
            _row("overview.irr_annualised", "2026-03-31", 0.0702, period="ytd")]  # differs; mtd is missing

    def test_fallback_uses_equal_figures_and_keeps_today_where_they_differ(self):
        answer, lines, counts = self._settle("fallback", FakeReader(self.ROWS))
        data = answer["data"]
        self.assertEqual(data["latest_nav"], 12705.004)  # the certified value, equal to today's
        self.assertEqual(data["periods"][0]["twr"], 0.0512 * (1 + 1e-12))
        self.assertEqual(data["periods"][0]["irr_annualized"], 0.07)  # today's: they differ
        self.assertEqual(data["periods"][1]["twr"], 0.0101)  # today's: nothing certified
        self.assertEqual(answer[core.SOURCE_FIELD], {"/data/periods/0/irr_annualized": "fallback"})
        figures = answer["certified"]["figures"]
        self.assertEqual([(f["figure_id"], f["at"]) for f in figures],
                         [("overview.nav", "/data/latest_nav"), ("overview.twr", "/data/periods/0/twr")])
        self.assertEqual(figures[1]["period_key"], "ytd")
        self.assertEqual(figures[1]["lineage"], {"build_id": BUILD})
        self.assertEqual(figures[0]["display"], "12,705.00 USD")
        self.assertIn("[F1]", answer["certified"]["note"])
        [line] = lines
        self.assertIn("WARNING", line)
        payload = json.loads(line.split(core.LOG_PREFIX, 1)[1])
        self.assertEqual(payload, {"tool": "get_performance", "mode": "fallback", "subject": MASKED, "status": "differ",
                                   "compared": 4, "used": 2, "differ": {"overview.irr_annualised": 1},
                                   "missing": {"overview.twr": 1}, "read_ms": payload["read_ms"]})
        for secret in (ACCOUNT, "0.07", "0.0702", "12705", "0.0101"):
            self.assertNotIn(secret, line)
        self.assertEqual(counts, {("get_performance", "overview.nav", "equal"): 1,
                                  ("get_performance", "overview.twr", "equal"): 1,
                                  ("get_performance", "overview.irr_annualised", "different"): 1,
                                  ("get_performance", "overview.twr", "missing"): 1})

    def test_exactly_equal_figures_keep_todays_spelling_and_log_nothing(self):
        rows = [_row("overview.nav", "2026-03-31", 12705.0), _row("overview.twr", "2026-03-31", 0.0512, period="ytd"),
                _row("overview.irr_annualised", "2026-03-31", 0.07, period="ytd"),
                _row("overview.twr", "2026-03-31", 0.0101, period="mtd")]
        answer, lines, _ = self._settle("fallback", FakeReader(rows))
        self.assertEqual(answer["data"], self._answer()["data"])
        self.assertEqual(len(answer["certified"]["figures"]), 4)
        self.assertNotIn(core.SOURCE_FIELD, answer)
        self.assertEqual(lines, [])

    def test_on_uses_the_certified_figure_even_when_it_differs(self):
        answer, lines, _ = self._settle("on", FakeReader(self.ROWS))
        self.assertEqual(answer["data"]["periods"][0]["irr_annualized"], 0.0702)
        self.assertEqual(answer["data"]["periods"][1]["twr"], 0.0101)
        self.assertNotIn(core.SOURCE_FIELD, answer)
        self.assertIn('"mode":"on"', lines[0])

    def test_a_failed_read_keeps_every_figure_and_flags_them(self):
        answer, lines, counts = self._settle("fallback", FakeReader(fail=RuntimeError("canceling statement")))
        self.assertEqual(answer["data"], self._answer()["data"])
        self.assertNotIn("certified", answer)
        self.assertEqual(set(answer[core.SOURCE_FIELD]), {s.at for s in self._slots(self._answer())})
        self.assertEqual(set(answer[core.SOURCE_FIELD].values()), {"fallback"})
        [line] = lines
        self.assertIn('"status":"error"', line)
        self.assertIn('"error":"RuntimeError"', line)
        self.assertNotIn("canceling", line)
        self.assertEqual(sum(n for (_, _, outcome), n in counts.items() if outcome == "error"), 4)

    def test_off_does_nothing(self):
        answer = self._answer()
        self.assertIs(core.settle("x", ACCOUNT, answer, self._slots(answer), _never, "off", logging.getLogger("x")),
                      answer)
        self.assertEqual(answer, self._answer())


class AgentToolsTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_env(**{core.SETTING: "fallback"}, **SERVED).stop)

    def test_risk_metrics_over_the_whole_history(self):
        today = _run(t.get_risk_metrics, {"from_date": "2000-01-01"}, FakeReader())
        data, as_of = today["data"], today["as_of"]
        reader = FakeReader([_row("overview.twr", as_of, data["total_return"], period="since_inception"),
                             _row("stats.maximum_drawdown", as_of, data["max_drawdown"] - 0.01),
                             _row("stats.best_day", as_of, data["best_day"]["return"]),
                             _row("stats.annualized_volatility", as_of, data["annualized_volatility"])])
        result = _run(t.get_risk_metrics, {"from_date": "2000-01-01"}, reader)
        self.assertEqual(result["data"]["max_drawdown"], data["max_drawdown"])  # differs: today's
        self.assertEqual(result[core.SOURCE_FIELD], {"/data/max_drawdown": "fallback"})
        self.assertEqual({f["figure_id"]: f["at"] for f in result["certified"]["figures"]},
                         {"overview.twr": "/data/total_return", "stats.best_day": "/data/best_day/return"})
        self.assertEqual(result["data"]["annualized_return"], data["annualized_return"])
        # Volatility is annualised by valuations a year here, by sqrt(252) when certified: never compared.
        self.assertNotIn("stats.annualized_volatility", reader.calls[0][0])
        self.assertEqual(sorted(reader.calls[0][0]), ["overview.twr", "stats.best_day", "stats.best_month",
                                                      "stats.maximum_drawdown", "stats.worst_day", "stats.worst_month"])

    def test_the_one_year_window_is_never_compared(self):
        result = _run(t.get_risk_metrics, {}, FakeReader(fail=AssertionError("read")))
        self.assertNotIn("certified", result)
        self.assertNotIn(core.SOURCE_FIELD, result)

    def test_performance_periods(self):
        today = _run(t.get_performance, {}, FakeReader())
        ytd = next(p for p in today["data"]["periods"] if p["key"] == "ytd")
        index = today["data"]["periods"].index(ytd)
        reader = FakeReader([_row("overview.twr", today["as_of"], ytd["twr"], period="ytd"),
                             _row("overview.nav", today["as_of"], today["data"]["latest_nav"])])
        with core.observe() as counts:
            result = _run(t.get_performance, {}, reader)
        self.assertEqual({(f["figure_id"], f.get("period_key")): f["at"] for f in result["certified"]["figures"]},
                         {("overview.twr", "ytd"): f"/data/periods/{index}/twr", ("overview.nav", None): "/data/latest_nav"})
        self.assertNotIn(core.SOURCE_FIELD, result)  # nothing differed; the rest is not published
        good = [p for p in today["data"]["periods"] if p["status"] not in ("fail", "empty")]
        yearly = [p for p in good if p["key"].startswith("year_") or p["key"] == "since_inception"]
        self.assertEqual(counts[("get_performance", "overview.net_external_flows", "missing")], len(good))
        self.assertEqual(counts[("get_performance", "overview.twr_annualised", "missing")], len(yearly))
        self.assertEqual(counts[("get_performance", "overview.irr_annualised", "missing")], len(good))
        with core.observe() as composite:  # a model portfolio has no investor flows: none certified
            _run(t.get_performance, {}, FakeReader(), flows="none")
        self.assertFalse([key for key in composite if key[1] == "overview.net_external_flows"])

    def test_figures_whose_page_is_off_are_compared_but_not_listed(self):
        os.environ[certified_tools.PAGES_SETTING] = ""
        today = _run(t.get_performance, {}, FakeReader())
        reader = FakeReader([_row("overview.nav", today["as_of"], today["data"]["latest_nav"])])
        result = _run(t.get_performance, {}, reader)
        self.assertTrue(reader.calls)
        self.assertNotIn("certified", result)

    def test_nav_history_lists_only_the_last_point(self):
        today = _run(t.get_nav_history, {"frequency": "monthly"}, FakeReader())
        points = today["data"]["points"]
        rows = [_row("overview.twr", p["date"], p["cumulative_return"], period="since_inception") for p in points[1:]]
        rows += [_row("overview.nav", p["date"], p["value"]) for p in points]
        rows[0] = _row("overview.twr", points[1]["date"], points[1]["cumulative_return"] + 0.5, period="since_inception")
        result = _run(t.get_nav_history, {"frequency": "monthly"}, FakeReader(rows))
        self.assertEqual({f["at"] for f in result["certified"]["figures"]},
                         {f"/data/points/{len(points) - 1}/value", f"/data/points/{len(points) - 1}/cumulative_return"})
        self.assertEqual(result[core.SOURCE_FIELD], {"/data/points/1/cumulative_return": "fallback"})
        later = _run(t.get_nav_history, {"from_date": DAYS[10]}, FakeReader(fail=AssertionError("read")))
        self.assertNotIn("certified", later)  # not from inception: its returns start elsewhere

    def test_sector_weights_by_the_websites_label(self):
        allocation = {"allocation": [{"sector": "equity_fund", "market_value": 3.0, "weight": 0.75},
                                     {"sector": "cash_sleeve", "market_value": 1.0, "weight": 0.25}]}
        reader = FakeReader([_row("holdings.sector_allocation", "2026-03-31", 0.75, item="Equity Fund"),
                             _row("holdings.sector_allocation", "2026-03-31", 0.2, item="Cash Sleeve")])
        with mock.patch.object(t, "_subject", return_value={**SUBJECT, "subject_type": "composite_portfolio"}), \
                mock.patch.object(t, "_latest_nav_date", return_value="2026-03-31"), \
                mock.patch.object(t, "_valued_holdings", return_value=("2026-03-30", [], allocation, True)), \
                mock.patch.object(certified_fallback, "reader", return_value=reader):
            result = t.get_sector_allocation(None, {"account_id": ACCOUNT})
        self.assertEqual(reader.calls[0][1], ["2026-03-31"])  # the NAV date, not the holdings date
        self.assertEqual([f["item"] for f in result["certified"]["figures"]], ["Equity Fund"])
        self.assertEqual(result[core.SOURCE_FIELD], {"/data/allocation/1/weight": "fallback"})
        self.assertEqual(result["data"]["allocation"][1]["weight"], 0.25)

    def test_benchmark_rows_by_period_and_excess_follows(self):
        periods = [{"key": "ytd", "label": "Year to date", "status": "pass"},
                   {"key": "mtd", "label": "Month to date", "status": "fail"}]
        answer = {"as_of": "2026-03-31", "data": {"periods": [
            {"period": "Year to date", "portfolio_return": 0.05, "benchmark_return": 0.03, "excess_return": 0.05 - 0.03},
            {"period": "Month to date", "portfolio_return": 0.01, "benchmark_return": None, "excess_return": None}]}}
        os.environ[core.SETTING] = "on"
        certified_fallback.apply(None, "compare_to_benchmark", ACCOUNT, answer,
                                 lambda: certified_fallback.benchmark_period_slots(answer, periods),
                                 certified_fallback.refresh_excess,
                                 read=FakeReader([_row("overview.twr", "2026-03-31", 0.06, period="ytd")]))
        row = answer["data"]["periods"][0]
        self.assertEqual((row["portfolio_return"], row["excess_return"]), (0.06, 0.06 - 0.03))

    def test_a_slot_that_cannot_be_built_leaves_the_answer(self):
        answer = {"as_of": "2026-03-31", "data": {}}
        with self.assertLogs("wealthryse_agent.tools", "WARNING") as logged:
            self.assertIs(certified_fallback.apply(None, "get_performance", ACCOUNT, answer, lambda: 1 / 0), answer)
        self.assertIn("ZeroDivisionError", logged.output[0])
        self.assertEqual(answer, {"as_of": "2026-03-31", "data": {}})


class SnapshotPathTest(unittest.TestCase):
    """The month-end snapshot builder and the pages call execute_tool without certified_mode."""

    def setUp(self):
        self.addCleanup(_env(**{core.SETTING: "on"}).stop)
        result = web_tools.AgentToolResult("get_sector_allocation", ACCOUNT, "2026-03-31", "pass",
                                           {"allocation": [{"sector": "Technology", "weight": 0.6}]}, (), "now")
        self.definition = web_tools.AgentToolDefinition("get_sector_allocation", "", {"properties": {
            "subject_id": {}, "as_of": {}}}, lambda *a: result)
        self.context = web_tools.AgentToolContext("t", "a", frozenset({ACCOUNT}), "c")

    def _execute(self, **kwargs):
        with mock.patch.dict(web_tools.TOOL_REGISTRY, {"get_sector_allocation": self.definition}):
            return web_tools.execute_tool(None, self.context, "get_sector_allocation", {"subject_id": ACCOUNT},
                                          **kwargs)

    def test_without_the_argument_nothing_is_read_even_when_the_setting_is_on(self):
        with mock.patch.object(core, "apply_to_result", side_effect=AssertionError("read")):
            payload = self._execute()
        self.assertEqual(payload["data"], {"allocation": [{"sector": "Technology", "weight": 0.6}]})

    def test_the_snapshot_builder_never_passes_it(self):
        source = inspect.getsource(reporting_snapshot)
        self.assertIn("execute_tool", source)
        self.assertNotIn("certified_mode", source)

    def test_with_the_argument_the_website_analyst_reads_them(self):
        reader = FakeReader([_row("holdings.sector_allocation", "2026-03-31", 0.61, item="Technology")])
        with mock.patch.object(core, "web_reader", return_value=reader):
            payload = self._execute(certified_mode="fallback")
        self.assertEqual(payload["data"]["allocation"][0]["weight"], 0.6)
        self.assertEqual(payload["data"][core.SOURCE_FIELD], {"/data/allocation/0/weight": "fallback"})
        self.assertNotIn("certified", payload["data"])  # its model sees sanitised fields only
        with mock.patch.object(core, "web_reader", return_value=FakeReader(
                [_row("holdings.sector_allocation", "2026-03-31", 0.6, item="Technology")])):
            self.assertEqual(self._execute(certified_mode="fallback")["data"],
                             {"allocation": [{"sector": "Technology", "weight": 0.6}]})

    def test_web_benchmark_and_risk_slots(self):
        result = web_tools.AgentToolResult("compare_with_benchmarks", ACCOUNT, "2026-03-31", "pass",
                                           {"benchmark": {"benchmark_id": "SPX-USD"},
                                            "metrics": {"beta": 0.9, "correlation": 0.8}}, (), "now")
        reader = FakeReader([_row("benchmark.beta", "2026-03-31", 0.9, benchmark_id="SPX-USD"),
                             _row("benchmark.correlation", "2026-03-31", 0.81, benchmark_id="SPX-USD")])
        changed = core.apply_to_result(result, tenant_id="t", mode_now="fallback", reader=reader)
        self.assertEqual(reader.calls[0][2], ["SPX-USD"])
        self.assertEqual(changed.data["metrics"], {"beta": 0.9, "correlation": 0.8})
        self.assertEqual(changed.data[core.SOURCE_FIELD], {"/data/metrics/correlation": "fallback"})
        self.assertEqual(result.data, {"benchmark": {"benchmark_id": "SPX-USD"}, "metrics": {"beta": 0.9, "correlation": 0.8}})
        risk = dataclasses.replace(result, tool_name="get_risk_metrics", data={"metrics": {"sharpe_ratio": 1.1}})
        same = core.apply_to_result(risk, tenant_id="t", mode_now="fallback",
                                    reader=FakeReader([_row("risk.sharpe_ratio", "2026-03-31", 1.1)]))
        self.assertIs(same, risk)


class MonthEndTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_env(**SERVED, **{certified_tools.MONTH_END_SETTING: "on"}).stop)

    def test_the_tool_server_offers_the_read_tools_to_the_month_end_job(self):
        self.assertTrue(CERTIFIED <= {x["name"] for x in tool_server.tool_list("month_end_drafter")})
        self.assertFalse({"get_month_end_package"} & {x["name"] for x in tool_server.tool_list("month_end_drafter")})
        os.environ[certified_tools.AGENT_SETTING] = ""
        self.assertFalse(CERTIFIED & {x["name"] for x in tool_server.tool_list("month_end_drafter")})

    def test_the_job_asks_for_them(self):
        seen = []
        with mock.patch.object(jobs, "_run_agent",
                               lambda agent, tools, account, task, allowed: seen.append((task, allowed)) or ("", [], {}, False)):
            jobs.month_end_drafter(SimpleNamespace(inbox_exists=lambda *a: False), None, None, [ACCOUNT], {},
                                   today=date(2026, 11, 2))
        task, allowed = seen[0]
        self.assertEqual(allowed[-2:], [certified_tools.GET, certified_tools.TRACE])
        self.assertTrue(task.endswith(MONTH_END_CERTIFIED.format(account_id=ACCOUNT, month_end="2026-10-31")))


# --- The [F#] check ----------------------------------------------------------------------------

FIGURE = {"figure_id": "overview.twr", "label": "Time-weighted return", "value": 0.0512, "unit": "ratio",
          "display": "5.12%", "as_of_date": "2026-03-31", "period_key": "ytd", "at": "/data/periods/0/twr",
          "definition": {"id": "metric-twr", "version": "abc"}, "lineage": {"build_id": BUILD}}
PERFORMANCE = {"tool": "get_performance", "account_id": ACCOUNT, "as_of": "2026-03-31",
               "data": {"periods": [{"key": "ytd", "twr": 0.0512, "irr_annualized": 0.0731}]},
               "certified": {"figures": [FIGURE], "note": core.NOTE}}


def _figures():
    figures = {}
    result = number_attached(json.loads(json.dumps(PERFORMANCE)), figures, "T1", "get_performance")
    return {"T1": result}, figures


class EnforcedCheckTest(unittest.TestCase):
    def test_warn_is_todays_check(self):
        results, figures = _figures()
        for answer in ("YTD was 5.12% [F1].", "YTD was 7.31% [F1].", "YTD was 5.12% [T1]."):
            check = validate_answer(answer, [], results, figures=figures)
            self.assertEqual(check, validate_answer(answer, [], results, figures=figures, figure_check="warn"))
            self.assertTrue(check.ok)
            self.assertEqual((check.quoted_elsewhere, check.figure_mode), ([], "warn"))

    def test_enforce_fails_a_wrong_number_beside_a_ref(self):
        results, figures = _figures()
        check = validate_answer("YTD was 7.31% [F1].", ["F1"], results, figures=figures, figure_check="enforce")
        self.assertFalse(check.ok)
        self.assertEqual(check.figure_checks, {"F1": "warning"})
        self.assertIn("Certified figures: F1 (overview.twr) is 5.12%", check.feedback())
        bad_ref = validate_answer("YTD was 5.12% [F7].", ["F7"], results, figures=figures, figure_check="enforce")
        self.assertFalse(bad_ref.ok)

    def test_enforce_fails_a_certified_figure_quoted_from_the_tool_call(self):
        results, figures = _figures()
        check = validate_answer("YTD was 5.12% [T1] and IRR 7.31% [T1].", ["T1"], results, figures=figures,
                                figure_check="enforce")
        self.assertFalse(check.ok)
        self.assertEqual((check.quoted_elsewhere, check.quoted_refs), (["5.12% is [F1]"], ["F1"]))
        self.assertIn("cite each by its ref instead: 5.12% is [F1]", check.feedback())
        right = validate_answer("YTD was 5.12% [F1] and IRR 7.31% [T1].", ["F1", "T1"], results, figures=figures,
                                figure_check="enforce")
        self.assertTrue(right.ok, right.feedback())

    def test_a_number_another_result_also_returned_is_allowed(self):
        results, figures = _figures()
        results["T1"]["data"]["periods"][0]["custom_twr"] = 0.0512
        check = validate_answer("YTD was 5.12% [T1].", ["T1"], results, figures=figures, figure_check="enforce")
        self.assertTrue(check.ok, check.feedback())

    def test_get_certified_figures_values_must_be_cited_by_ref(self):
        figure = {**FIGURE, "ref": "F1"}
        results = {"T1": {"tool": certified_tools.GET, "account_id": ACCOUNT, "figures": [figure]}}
        figures = {"F1": {**figure, "tool_call": "T1"}}
        self.assertFalse(validate_answer("YTD 5.1% [T1].", ["T1"], results, figures=figures, figure_check="enforce").ok)
        self.assertTrue(validate_answer("YTD 5.1% [F1].", ["F1"], results, figures=figures, figure_check="enforce").ok)

    def test_attached_figures_are_numbered_and_cited_with_their_tool(self):
        results, figures = _figures()
        self.assertEqual(results["T1"]["certified"]["figures"][0]["ref"], "F1")
        self.assertEqual((figures["F1"]["tool_call"], figures["F1"]["tool"], figures["F1"]["subject"]),
                         ("T1", "get_performance", ACCOUNT))
        [entry] = figure_citations("YTD 5.12% [F1].", [], figures, {"F1": "ok"})
        self.assertEqual((entry["tool"], entry["figure"]["period"], entry["figure"]["build_id"]),
                         ("get_performance", "ytd", BUILD))


def _settings():
    return ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                         region="southeastasia", processing_scope="apac-data-zone", managed_identity_client_id="x",
                         max_tool_calls=3)


def _call(name, arguments):
    return SimpleNamespace(status="completed", usage=SimpleNamespace(input_tokens=1, output_tokens=1), output_text="",
                           output=[SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments),
                                                   call_id="c1", model_dump=lambda **_: {
                                                       "type": "function_call", "name": name,
                                                       "arguments": json.dumps(arguments), "call_id": "c1"})])


def _final(answer, citations):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")],
                              model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
    return SimpleNamespace(status="completed", output=[message], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                           output_text=json.dumps({"answer": answer, "citations": citations}))


class RuntimeEnforceTest(unittest.TestCase):
    def _turn(self, *answers, check="enforce"):
        responses = [_call("get_performance", {"account_id": ACCOUNT}), *(_final(a, c) for a, c in answers)]
        model = SimpleNamespace(requests=[])
        model.responses = SimpleNamespace(create=lambda **kw: (model.requests.append(kw), responses.pop(0))[1])
        client = SimpleNamespace(list_tools=lambda: [{"name": "get_performance", "description": "",
                                                      "inputSchema": t.REGISTRY["get_performance"].parameters}],
                                 call=lambda name, arguments: json.loads(json.dumps(PERFORMANCE)))
        with _env(**{certified_tools.CHECK_SETTING: check}), \
                self.assertLogs("wealthryse_agent.runtime", "INFO") as logged:
            logging.getLogger("wealthryse_agent.runtime").info("start")
            events = list(Agent(_settings(), model_client=model).run(
                question="How did it do?", account_id=ACCOUNT, tools=client, skip_router=True))
        return events, model, [line for line in logged.output if "CERTIFIED_FIGURE_CHECK" in line]

    def test_a_failed_check_is_sent_back_once_then_shown_when_right(self):
        events, model, lines = self._turn(("YTD was 5.12% [T1].", ["T1"]), ("YTD was 5.12% [F1].", ["F1"]))
        self.assertEqual(events[-1]["type"], "answer")
        self.assertEqual(events[-1]["answer"], "YTD was 5.12% [F1].")
        self.assertIn("cite each by its ref instead", model.requests[-1]["input"][-1]["content"])
        [line] = lines
        self.assertEqual(json.loads(line.split("CERTIFIED_FIGURE_CHECK ", 1)[1]),
                         {"mode": "enforce", "outcome": "retry", "warnings": 0, "quoted_elsewhere": 1,
                          "refs": ["F1"], "figure_ids": ["overview.twr"]})
        certified = [c for c in events[-1]["citations"] if c.get("certified")]
        self.assertEqual([(c["id"], c["tool"], c["check"]) for c in certified], [("F1", "get_performance", "ok")])

    def test_a_second_failure_is_blocked(self):
        events, _, lines = self._turn(("YTD was 7.31% [F1].", ["F1"]), ("YTD was 7.31% [F1].", ["F1"]))
        self.assertEqual((events[-1]["type"], events[-1]["answer"]), ("rejected", UNVERIFIED))
        self.assertEqual(events[-1]["turn"].status, "rejected")
        self.assertEqual([json.loads(line.split("CERTIFIED_FIGURE_CHECK ", 1)[1])["outcome"] for line in lines],
                         ["retry", "rejected"])
        for line in lines:
            self.assertNotIn("7.31", line)
            self.assertNotIn(ACCOUNT, line)

    def test_warn_shows_the_answer_and_logs_as_today(self):
        events, _, lines = self._turn(("YTD was 7.31% [F1].", ["F1"]), check="")
        self.assertEqual(events[-1]["type"], "answer")
        self.assertEqual(events[-1]["turn"].figure_warnings, ["F1"])
        self.assertEqual(json.loads(lines[0].split("CERTIFIED_FIGURE_CHECK ", 1)[1]),
                         {"warnings": 1, "refs": ["F1"], "figure_ids": ["overview.twr"]})


if __name__ == "__main__":
    unittest.main()
