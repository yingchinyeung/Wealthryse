"""The agents' certified figures tools (U5 part 7): JSON shape, switches, [F#] citations.

No database or network: the tools run over a fake connection with synthetic rows.
"""
from __future__ import annotations

import json
import os
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from ibkr_etl import certified_figures as register  # noqa: E402
from wealthryse_agent import certified_tools, guide_tools, tool_server  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.runtime import CERTIFIED_NOTE, Agent, figure_citations, number_figures  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402
from wealthryse_agent.tools import REGISTRY, TOOLS, ToolError, run_tool  # noqa: E402
from wealthryse_agent.validator import check_figure_citations, validate_answer  # noqa: E402

ON = {certified_tools.AGENT_SETTING: "on", certified_tools.PAGES_SETTING: "overview=on,risk-snapshot=on"}
OFF = {certified_tools.AGENT_SETTING: "", certified_tools.PAGES_SETTING: ""}
CERTIFIED = {certified_tools.GET, certified_tools.TRACE}
BUILD = "5d0c0000-0000-4000-8000-000000000001"
RUN = "9b410000-0000-4000-8000-000000000002"
PUBLISHED = datetime(2026, 10, 22, 9, 52, 11, tzinfo=timezone.utc)
DAY = date(2026, 10, 21)


def _row(figure_id, value, unit="number", **extra):
    item = register.figure(figure_id)
    row = {"figure_id": figure_id, "period": "", "item": "", "benchmark_id": "", "as_of_date": DAY,
           "value": value if unit not in ("date", "text") else None,
           "value_text": value if unit in ("date", "text") else None, "unit": unit, "currency": None,
           "status": "ok", "reason": None, "period_start": date(2025, 1, 21), "period_end": DAY, "observations": 438,
           "definition_id": item.definition_id, "definition_version": item.definition_version,
           "calculation_version": "risk-analytics-v2+benchmark-comparison-v1",
           "source_table": "gold.risk_analytics_snapshots",
           "source_key": {"as_of_date": "2026-10-21", "analytics_version": "risk-analytics-v2"},
           "build_id": BUILD, "lineage_run_id": RUN, "build_status": "published", "published_at": PUBLISHED}
    row.update(extra)
    return row


class FakeConnection:
    """Answers the certified tools' queries from synthetic rows; records what was asked."""

    def __init__(self, rows=(), latest="2026-10-21", builds=(), trace=None):
        self.rows, self.latest, self.builds, self.trace = list(rows), latest, list(builds), trace
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((" ".join(sql.split()), params))
        if "subject_nav_series" in sql:
            found = [{"latest": self.latest}]
        elif "platform_subjects" in sql:
            found = [{"subject_id": params[0], "subject_type": "physical_account", "display_name": "Synthetic",
                      "base_currency": "USD", "reporting_currency": "USD", "history_start_date": None,
                      "source_account_id": None}]
        elif "MAX(as_of_date)" in sql:
            days = [r["as_of_date"] for r in self.rows if r["figure_id"] in params[1]
                    and (params[2] is None or r["as_of_date"].isoformat() <= params[2])]
            found = [{"d": max(days) if days else None}]
        elif "portfolio_benchmark_mapping" in sql:
            found = [{"benchmark_id": "SPY-TR"}]
        elif "certified_builds" in sql:
            found = self.builds
        elif "certified_figure_trace" in sql:
            found = [{"trace": self.trace}]
        elif "FROM certified_figures" in sql:
            found = sorted((r for r in self.rows if r["figure_id"] in params[1]
                            and r["as_of_date"].isoformat() == params[2]), key=lambda r: r["figure_id"])
        else:
            raise AssertionError(f"unexpected query: {sql}")
        return SimpleNamespace(fetchone=lambda: found[0] if found else None, fetchall=lambda: list(found))


class SwitchesAreOffByDefaultTest(unittest.TestCase):
    """With AGENT_CERTIFIED_FIGURES unset every agent, tool and job behaves exactly as today."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, OFF)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (certified_tools.AGENT_SETTING, certified_tools.PAGES_SETTING):
            os.environ.pop(name, None)

    def test_the_setting_must_say_on(self):
        self.assertFalse(certified_tools.enabled({}))
        for value in ("", "off", "shadow", "no", "0"):
            self.assertFalse(certified_tools.enabled({certified_tools.AGENT_SETTING: value}), value)
        for value in ("on", "ON", "true", "1", "yes"):
            self.assertTrue(certified_tools.enabled({certified_tools.AGENT_SETTING: value}), value)
        self.assertEqual(certified_tools.served({certified_tools.PAGES_SETTING: "overview=on"}), {})

    def test_tool_lists_are_todays_for_people_and_every_job(self):
        def today(job):  # the rule before the certified tools existed
            return [t.name for t in TOOLS if t.name not in CERTIFIED and (not t.only_jobs or job in t.only_jobs)]

        for job in (None, "month_end_drafter", "operations", "guide_check"):
            self.assertEqual([t["name"] for t in tool_server.tool_list(job)], today(job), job)

    def test_hidden_tools_cannot_be_called(self):
        for name in CERTIFIED:
            with self.assertRaises(ToolError) as caught:
                run_tool(FakeConnection(), name, {"account_id": "U1", "figure_id": "overview.nav"})
            self.assertEqual(caught.exception.code, "unknown_tool")

    def test_guide_tools_are_unchanged(self):
        wrapped = guide_tools.GuideTools()
        self.assertEqual([t["name"] for t in wrapped.list_tools()], list(guide_tools.NAMES))
        with self.assertRaises(ToolCallFailed):
            wrapped.call(certified_tools.LIST, {})

    def test_the_analyst_prompt_is_unchanged(self):
        model = SimpleNamespace(requests=[])

        def create(**kwargs):
            model.requests.append(kwargs)
            return SimpleNamespace(status="completed", output=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                   output_text=json.dumps({"answer": "Hello.", "citations": []}))

        model.responses = SimpleNamespace(create=create)
        listed = SimpleNamespace(list_tools=lambda: [{"name": n, "description": s.description,
                                                      "inputSchema": s.parameters} for n, s in REGISTRY.items()])
        list(Agent(_settings(), model_client=model).run(question="Hi", account_id="U1", tools=listed, skip_router=True))
        self.assertNotIn(CERTIFIED_NOTE.strip(), model.requests[0]["instructions"])

    def test_validator_without_certified_figures_is_unchanged(self):
        results = {"T1": {"tool": "get_performance", "account_id": "U1", "data": {"twr": 0.0512}}}
        for answer, cited in (("Up 5.1% [T1].", ["T1"]), ("Up 9% [T1].", ["T1"]), ("Up 5.1% [F1].", ["F1"])):
            before = validate_answer(answer, cited, results)
            self.assertEqual(before, validate_answer(answer, cited, results, figures=None))
            self.assertEqual(before.figure_warnings, [])


class ToolsWhenOnTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ON)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_offered_to_people_and_jobs_but_not_the_month_end_commentary(self):
        self.assertTrue(CERTIFIED <= {t["name"] for t in tool_server.tool_list(None)})
        self.assertTrue(CERTIFIED <= {t["name"] for t in tool_server.tool_list("guide_check")})
        self.assertFalse(CERTIFIED & {t["name"] for t in tool_server.tool_list("month_end_drafter")})
        for name in CERTIFIED:
            self.assertFalse(REGISTRY[name].parameters["additionalProperties"])

    def test_json_shape_as_in_the_design(self):
        connection = FakeConnection([_row("risk.sharpe_ratio", 1.12),
                                     _row("overview.nav", 125000.5, "money", currency="USD"),
                                     _row("risk.risk_free_rate", None, status="unavailable",
                                          reason="No validated rate on or before 21 Oct 2026.")])
        result = run_tool(connection, certified_tools.GET,
                          {"account_id": "U1", "figures": ["risk.sharpe_ratio", "overview.nav", "risk.risk_free_rate"]})
        self.assertEqual(result["tool"], "get_certified_figures")
        self.assertEqual(result["account_id"], "U1")
        self.assertEqual(result["as_of"], "2026-10-21")
        self.assertEqual(result["subject"], {"id": "U1", "type": "physical_account", "reporting_currency": "USD"})
        nav, sharpe = result["figures"]  # in figure id order
        self.assertEqual(set(sharpe), {"ref", "figure_id", "label", "value", "unit", "display", "as_of_date", "period",
                                       "definition", "calculation_version", "freshness", "lineage", "build"})
        self.assertEqual((sharpe["ref"], sharpe["figure_id"], sharpe["label"], sharpe["value"], sharpe["display"]),
                         ("F2", "risk.sharpe_ratio", "Sharpe ratio", 1.12, "1.12"))
        self.assertEqual(sharpe["period"], {"start": "2025-01-21", "end": "2026-10-21", "observations": 438,
                                            "basis": "benchmark dates"})
        self.assertEqual(sharpe["definition"]["id"], "metric-sharpe-ratio")
        self.assertEqual(sharpe["definition"]["version"], register.figure("risk.sharpe_ratio").definition_version)
        self.assertEqual(sharpe["definition"]["link"], "/metrics-guide#metric-sharpe-ratio")
        self.assertTrue(sharpe["definition"]["what_it_tells_you"])
        self.assertEqual(sharpe["freshness"], {"status": "fresh", "latest_data_date": "2026-10-21",
                                               "business_days_behind": 0, "promise": register.FRESHNESS_PROMISE})
        self.assertEqual(sharpe["lineage"], {"build_id": BUILD, "run_id": RUN, "source": {
            "table": "gold.risk_analytics_snapshots",
            "key": {"as_of_date": "2026-10-21", "analytics_version": "risk-analytics-v2"}}})
        self.assertEqual(sharpe["build"], {"status": "published", "published_at": "2026-10-22T09:52:11+00:00",
                                           "publish_gate": "shadow"})
        self.assertEqual((nav["currency"], nav["display"]), ("USD", "125,000.50 USD"))
        self.assertEqual(result["unavailable"], [{"figure_id": "risk.risk_free_rate", "period": None, "item": None,
                                                  "reason": "No validated rate on or before 21 Oct 2026."}])
        self.assertEqual(result["citations"][0]["build_ids"], [BUILD])
        json.dumps(result)  # plain JSON, as the tool server sends it

    def test_a_held_build_is_behind_not_an_error(self):
        connection = FakeConnection([_row("risk.sharpe_ratio", 1.12)], latest="2026-10-23",
                                    builds=[{"status": "held", "started_at": datetime(2026, 10, 24, 9, tzinfo=timezone.utc)}])
        [figure] = run_tool(connection, certified_tools.GET, {"account_id": "U1", "figures": ["risk.sharpe_ratio"]})["figures"]
        self.assertEqual(figure["freshness"]["status"], "behind")
        self.assertEqual(figure["freshness"]["business_days_behind"], 2)
        self.assertIn("held for review", figure["freshness"]["reason"])

    def test_a_page_asks_for_its_served_figures(self):
        connection = FakeConnection([_row("overview.nav", 10.0, "money"), _row("overview.as_of", "2026-10-21", "date")])
        result = run_tool(connection, certified_tools.GET, {"account_id": "U1", "page": "overview"})
        self.assertEqual([f["figure_id"] for f in result["figures"]], ["overview.as_of", "overview.nav"])
        asked = [params for sql, params in connection.queries if "SELECT figure_id" in sql][0]
        self.assertNotIn("holdings.holding_weight", asked[1])  # shown on Overview, but its own page is off

    def test_plain_errors(self):
        cases = [({"figures": ["risk.sharp_ratio"]}, "unknown_figure", "risk.sharpe_ratio"),
                 ({"figures": ["holdings.holding_weight"]}, "page_not_moved", "get_holdings"),
                 ({"page": "performance"}, "page_not_moved", "compare_to_benchmark"),
                 ({"figures": ["overview.nav"], "page": "overview"}, "invalid_arguments", "not both"),
                 ({}, "invalid_arguments", "not both"),
                 ({"figures": ["overview.nav"], "as_of": "2020-01-01"}, "no_certified_figures", "10:00 UTC"),
                 ({"figures": ["overview.nav"], "period": "ytd"}, "invalid_arguments", "period")]
        for arguments, code, words in cases:
            with self.assertRaises(ToolError) as caught:
                run_tool(FakeConnection([_row("overview.nav", 1.0, "money")]), certified_tools.GET,
                         {"account_id": "U1", **arguments})
            self.assertEqual(caught.exception.code, code, arguments)
            self.assertIn(words, caught.exception.message, arguments)

    def test_trace_reads_the_scoped_trace_function(self):
        trace = {"build": {"build_id": BUILD, "status": "published"}, "recorded": True,
                 "source": {"table": "gold.risk_analytics_snapshots", "key": {}},
                 "runs": [{"depth": 0, "basis": "row", "recorded": True, "job": "certified-figures"},
                          {"depth": 1, "basis": "dataset-time", "recorded": True, "job": "risk-analytics",
                           "inputs": [{"dataset": "raw file", "row_count": None, "sha256": "ab" * 32}]}],
                 "ibkr_statements": []}
        connection = FakeConnection([_row("risk.sharpe_ratio", 1.12)], trace=trace)
        result = run_tool(connection, certified_tools.TRACE, {"account_id": "U1", "figure_id": "risk.sharpe_ratio"})
        self.assertEqual(result["data"]["figure_id"], "risk.sharpe_ratio")
        self.assertEqual(result["data"]["runs"][1]["basis"], "dataset-time")
        self.assertIn("inferred from timing", result["data"]["how_to_read"])
        sql, params = [q for q in connection.queries if "certified_figure_trace" in q[0]][0]
        self.assertEqual(params, ("U1", "risk.sharpe_ratio", "", "", "", "2026-10-21"))
        with self.assertRaises(ToolError) as caught:
            run_tool(FakeConnection([_row("risk.sharpe_ratio", 1.12)]), certified_tools.TRACE,
                     {"account_id": "U1", "figure_id": "risk.sharpe_ratio"})
        self.assertEqual(caught.exception.code, "figure_not_found")

    def test_list_sits_with_the_guide_tools_and_reads_no_client_data(self):
        wrapped = guide_tools.GuideTools()
        self.assertEqual([t["name"] for t in wrapped.list_tools()], [*guide_tools.NAMES, certified_tools.LIST])
        result = wrapped.call(certified_tools.LIST, {})
        rows = result["data"]["figures"]
        self.assertEqual(len(rows), len(register.figures()))
        self.assertEqual(result["data"]["served"],
                         sum(1 for f in register.figures().values() if f.page in ("overview", "risk-snapshot")))
        sharpe = next(r for r in rows if r["figure_id"] == "risk.sharpe_ratio")
        self.assertTrue(sharpe["served"])
        self.assertFalse(next(r for r in rows if r["figure_id"] == "holdings.holding_weight")["served"])
        self.assertIsNone(result["account_id"])
        self.assertEqual(len(wrapped.call(certified_tools.LIST, {"page": "cash"})["data"]["figures"]), 2)
        with self.assertRaises(ToolCallFailed):
            wrapped.call(certified_tools.LIST, {"page": "nowhere"})


class FigureCitationTest(unittest.TestCase):
    FIGURES = {"F1": {"figure_id": "risk.sharpe_ratio", "value": 1.12, "unit": "number", "display": "1.12"},
               "F2": {"figure_id": "risk.annualized_volatility", "value": 0.1534, "unit": "ratio", "display": "15.34%"},
               "F3": {"figure_id": "overview.nav", "value": 1234567.0, "unit": "money", "display": "1,234,567.00 USD"},
               "F4": {"figure_id": "overview.as_of", "value": "2026-10-21", "unit": "date", "display": "2026-10-21"}}

    def test_the_number_beside_each_citation_must_be_that_figure(self):
        warnings, checks = check_figure_citations(
            "Sharpe was 1.12 [F1], volatility 15.3% [F2]; NAV $1.2m [F3] as of 2026-10-21 [F4].", self.FIGURES)
        self.assertEqual((warnings, checks), ([], {"F1": "ok", "F2": "ok", "F3": "ok", "F4": "ok"}))
        warnings, checks = check_figure_citations("Volatility was 1.12 [F2]. Odd [F9].", self.FIGURES)
        self.assertEqual(checks, {"F2": "warning"})
        self.assertEqual(len(warnings), 2)

    def test_the_check_warns_and_never_rejects(self):
        results = {"T1": {"tool": certified_tools.GET, "figures": list(self.FIGURES.values())}}
        right = validate_answer("Sharpe was 1.12 [F1].", ["F1"], results, figures=self.FIGURES)
        self.assertTrue(right.ok)
        self.assertEqual(right.figure_warnings, [])
        swapped = validate_answer("Volatility was 1.12 [F2].", ["F2"], results, figures=self.FIGURES)
        self.assertTrue(swapped.ok)  # 1.12 is a returned number, so only the [F#] check notices
        self.assertEqual(swapped.figure_checks, {"F2": "warning"})
        invented = validate_answer("Sharpe was 9.99 [F1].", ["F1"], results, figures=self.FIGURES)
        self.assertFalse(invented.ok)  # an invented number is rejected, as today

    def test_runtime_numbers_figures_across_the_turn_and_keeps_what_traces_them(self):
        figures = {}
        first = number_figures({"account_id": "U1", "figures": [{"figure_id": "a.x", "ref": "F1"}]}, figures, "T1")
        second = number_figures({"account_id": "U1", "figures": [{"figure_id": "b.y", "ref": "F1"},
                                                                 {"figure_id": "c.z", "ref": "F2"}]}, figures, "T2")
        self.assertEqual([f["ref"] for f in first["figures"] + second["figures"]], ["F1", "F2", "F3"])
        self.assertEqual(figures["F3"]["tool_call"], "T2")
        figures["F2"].update(value=1.12, unit="number", as_of_date="2026-10-21", period_key="ytd",
                             definition={"id": "metric-sharpe-ratio", "version": "7ab492a4026b"},
                             lineage={"build_id": BUILD})
        [entry] = figure_citations("Up 1.12 [F2].", [], figures, {"F2": "ok"})
        self.assertEqual((entry["id"], entry["certified"], entry["check"], entry["account_id"]), ("F2", True, "ok", "U1"))
        self.assertEqual(entry["figure"]["build_id"], BUILD)
        self.assertEqual(entry["figure"]["definition_version"], "7ab492a4026b")
        self.assertEqual(entry["figure"]["period"], "ytd")

    def test_agent_turn_with_certified_figures(self):
        figure = {"figure_id": "risk.sharpe_ratio", "label": "Sharpe ratio", "value": 1.12, "unit": "number",
                  "display": "1.12", "as_of_date": "2026-10-21", "lineage": {"build_id": BUILD},
                  "definition": {"id": "metric-sharpe-ratio", "version": "7ab492a4026b"}}
        vol = {**figure, "figure_id": "risk.annualized_volatility", "value": 0.1534, "unit": "ratio", "display": "15.34%"}
        result = {"tool": certified_tools.GET, "account_id": "U1", "as_of": "2026-10-21",
                  "figures": [{**figure, "ref": "F1"}, {**vol, "ref": "F2"}], "citations": []}
        calls = [_call(certified_tools.GET, {"account_id": "U1", "figures": ["risk.sharpe_ratio"]}),
                 _final("Sharpe 1.12 [F1]; volatility 1.12 [F2].", ["F1", "F2"])]
        model = SimpleNamespace(requests=[])
        model.responses = SimpleNamespace(create=lambda **kw: (model.requests.append(kw), calls.pop(0))[1])
        client = SimpleNamespace(list_tools=lambda: [{"name": certified_tools.GET, "description": "",
                                                      "inputSchema": REGISTRY[certified_tools.GET].parameters}],
                                 call=lambda name, arguments: result)
        with mock.patch.dict(os.environ, ON), self.assertLogs("wealthryse_agent.runtime", "WARNING") as logged:
            events = list(Agent(_settings(), model_client=model).run(
                question="How risky?", account_id="U1", tools=client, skip_router=True))
        self.assertIn(CERTIFIED_NOTE.strip(), model.requests[0]["instructions"])
        answer = events[-1]
        self.assertEqual(answer["type"], "answer")  # warn-only: shown despite the swapped figure
        self.assertEqual(answer["turn"].figure_warnings, ["F2"])
        certified = {c["id"]: c for c in answer["citations"] if c.get("certified")}
        self.assertEqual((certified["F1"]["check"], certified["F2"]["check"]), ("ok", "warning"))
        line = "\n".join(logged.output)
        self.assertIn("CERTIFIED_FIGURE_CHECK", line)
        self.assertNotIn("1.12", line)  # refs and figure ids only, never values
        self.assertNotIn("U1", line)


def _settings():
    return ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                         region="southeastasia", processing_scope="apac-data-zone", managed_identity_client_id="x",
                         max_tool_calls=3)


def _call(name, arguments, call_id="c1"):
    return SimpleNamespace(status="completed", usage=SimpleNamespace(input_tokens=1, output_tokens=1), output_text="",
                           output=[SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments),
                                                   call_id=call_id, model_dump=lambda **_: {
                                                       "type": "function_call", "name": name,
                                                       "arguments": json.dumps(arguments), "call_id": call_id})])


def _final(answer, citations):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")],
                              model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
    return SimpleNamespace(status="completed", output=[message], usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                           output_text=json.dumps({"answer": answer, "citations": citations}))


if __name__ == "__main__":
    unittest.main()
