"""The supervisor's registry, routing rules, watch-only task record and shadow report (no database or network)."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from wealthryse_agent import calculator, certified_tools, guide_tools  # noqa: E402
from wealthryse_agent.runtime import AgentError, Turn  # noqa: E402
from wealthryse_agent.supervisor import registry, report, watch  # noqa: E402
from wealthryse_agent.supervisor.routing import agreement, route_chat  # noqa: E402
from wealthryse_agent.tools import REGISTRY  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
USER = "22222222-2222-2222-2222-222222222222"
BUILD_A, BUILD_B = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"


def _install_script():
    spec = importlib.util.spec_from_file_location("install_agent_schema", ROOT / "scripts" / "install_agent_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chat_tools() -> set[str]:
    """Every tool website chat can call: the tool server's people tools, the guide, the register, the calculator."""
    served = {name for name, spec in REGISTRY.items() if not spec.only_jobs}
    return served | set(guide_tools.NAMES) | {certified_tools.LIST, calculator.NAME}


class RegistryTests(unittest.TestCase):
    def test_every_tool_has_a_medallion_layer(self):
        tools = set(REGISTRY) | set(guide_tools.NAMES) | {guide_tools.DRIFT, certified_tools.LIST, calculator.NAME}
        self.assertEqual(tools - set(registry.TOOL_LAYERS), set())
        self.assertEqual(set(registry.TOOL_LAYERS) - tools, set(), "a layer for a tool that does not exist")
        self.assertLessEqual(set(registry.TOOL_LAYERS.values()), set(registry.LAYERS))

    def test_certified_figures_are_gold_and_the_live_feed_is_bronze(self):
        self.assertEqual(registry.TOOL_LAYERS[certified_tools.GET], "gold")
        self.assertEqual(registry.TOOL_LAYERS[certified_tools.TRACE], "gold")
        self.assertEqual(registry.TOOL_LAYERS["get_month_end_snapshot"], "gold")
        self.assertEqual(registry.TOOL_LAYERS["get_performance"], "silver")
        self.assertEqual(registry.TOOL_LAYERS["get_live_snapshot"], "bronze")

    def test_every_job_is_exactly_one_member(self):
        jobs = set(_install_script().JOBS) | {"guide_keeper"}
        for spec in REGISTRY.values():
            jobs |= set(spec.only_jobs) | set(spec.not_jobs)
        claimed = [job for member in registry.MEMBERS for job in member.jobs]
        self.assertEqual(sorted(claimed), sorted(set(claimed)), "a job claimed twice")
        self.assertEqual(jobs - set(claimed), set(), "a job with no place in the hierarchy")

    def test_members_are_well_formed_and_none_can_act(self):
        names = [m.name for m in registry.MEMBERS]
        self.assertEqual(len(names), len(set(names)))
        for member in registry.MEMBERS:
            self.assertRegex(member.name, r"^[a-z_]{1,40}$")
            self.assertIn(member.team, registry.TEAMS)
            self.assertIn(member.kind, registry.KINDS)
            self.assertIn(member.autonomy, registry.AUTONOMY)
            self.assertIn(member.deployment, registry.DEPLOYMENTS)
            for tool in member.tools or ():
                self.assertIn(tool, registry.TOOL_LAYERS, f"{member.name} lists an unknown tool {tool}")
        self.assertNotIn("act", registry.AUTONOMY)
        for team in registry.TEAMS:
            self.assertEqual(sum(1 for m in registry.MEMBERS if m.team == team and m.kind == "lead"), 1, team)

    def test_client_analysis_stays_on_the_analyst_deployment(self):
        for member in registry.MEMBERS:
            if member.team == "client_analysis":
                self.assertEqual(member.deployment, "analyst", member.name)

    def test_every_chat_tool_belongs_to_one_specialist_or_is_shared(self):
        owners: dict[str, list[str]] = {}
        for name in registry.CLIENT_SPECIALISTS:
            for tool in registry.BY_NAME[name].tools:
                if tool not in registry.SHARED_CLIENT_TOOLS and tool not in registry.BORROWED.get(name, ()):
                    owners.setdefault(tool, []).append(name)
        for name, borrowed in registry.BORROWED.items():
            for tool in borrowed:
                self.assertIn(tool, registry.BY_NAME[name].tools)
                self.assertNotIn(registry.owner(tool), (None, name), f"{name} borrows {tool} from nobody")
        for tool in _chat_tools():
            if tool in registry.SHARED_CLIENT_TOOLS or tool in registry.SERVER_TOOLS:
                self.assertIsNone(registry.owner(tool))
                continue
            self.assertEqual(len(owners.get(tool, [])), 1, f"{tool} needs exactly one client specialist")
            self.assertEqual(registry.owner(tool), owners[tool][0])

    def test_client_specialists_reach_no_job_only_tool_and_no_bronze_but_the_live_snapshot(self):
        for name in registry.CLIENT_SPECIALISTS:
            for tool in registry.BY_NAME[name].tools:
                self.assertIn(tool, _chat_tools(), f"{name} lists {tool}, which chat cannot call")
                if registry.TOOL_LAYERS[tool] == "bronze":
                    self.assertEqual(tool, "get_live_snapshot")

    def test_layers_counts_calls(self):
        self.assertEqual(registry.layers(["get_performance", "get_holdings", certified_tools.GET, "nope"]),
                         {"silver": 2, "gold": 1, "unknown": 1})


class RoutingTests(unittest.TestCase):
    CASES = {
        "How did I do this year?": ("holdings_performance",),
        "What are my top 5 holdings?": ("holdings_performance",),
        "How much cash did I deposit last month?": ("holdings_performance",),
        "What is my Sharpe ratio?": ("risk",),
        "Show my value at risk": ("risk",),
        "What is a Sharpe ratio?": ("metrics_guide",),
        "How is the Sharpe ratio calculated?": ("metrics_guide",),
        "What does drawdown mean?": ("metrics_guide",),
        "What if the Hang Seng fell 20%?": ("modelling",),
        "What would my returns be if the S&P fell 30%?": ("modelling",),
        "Project my portfolio value in 10 years": ("modelling",),
        "How did I do this year, and what drove the risk?": ("holdings_performance", "risk"),
        "How is my Sharpe ratio calculated?": ("risk", "metrics_guide"),
        # Risk as what a model reports goes to modelling alone; a separate ask about risk keeps the risk specialist.
        "Which holdings carry the most risk compared with their weight?": ("modelling",),
        "What would the risk look like if my largest holding were half its weight?": ("modelling",),
        "If my largest holding fell 30%, what happens?": ("modelling",),
        "What is my risk, and what if tech falls 20%?": ("risk", "modelling"),
        "What would my value at risk be if I halved Apple?": ("risk", "modelling"),
    }

    def test_rules_place_typical_questions(self):
        for question, expected in self.CASES.items():
            with self.subTest(question=question):
                route = route_chat(question)
                self.assertEqual(route.specialists, expected)
                self.assertEqual((route.team, route.decided_by), ("client_analysis", "rules"))

    def test_one_area_takes_the_fast_path_and_several_take_the_planner(self):
        self.assertEqual(route_chat("What is my Sharpe ratio?").lead, "fast_path")
        self.assertEqual(route_chat("How did I do this year, and what drove the risk?").lead, "planner")
        unplaced = route_chat("hello there")
        self.assertEqual((unplaced.specialists, unplaced.lead, unplaced.decided_by), ((), "router", "router"))

    def test_advice_is_flagged(self):
        self.assertTrue(route_chat("Should I sell Tesla?").advice)
        self.assertFalse(route_chat("What if I sold half my holdings?").advice)

    def test_agreement(self):
        self.assertEqual(agreement(("risk",), ["risk"]), "agree")
        self.assertEqual(agreement((), []), "undecided")
        self.assertEqual(agreement(("risk", "modelling"), ["risk"]), "over")
        self.assertEqual(agreement(("risk",), ["risk", "modelling"]), "under")
        self.assertEqual(agreement(("risk", "metrics_guide"), ["risk", "modelling"]), "partial")
        self.assertEqual(agreement(("risk",), ["modelling"]), "differ")
        self.assertEqual(agreement((), ["risk"]), "undecided")
        self.assertEqual(agreement(("risk",), []), "no_tools")


class _Inner:
    def __init__(self, results=None, fail=()):
        self.results, self.fail, self.closed = results or {}, set(fail), False

    def list_tools(self):
        return [{"name": "get_performance"}]

    def call(self, name, arguments):
        if name in self.fail:
            from wealthryse_agent.tool_client import ToolCallFailed
            raise ToolCallFailed("boom", "failed")
        return self.results.get(name, {"data": {}})

    def close(self):
        self.closed = True


def _certified(ref, build):
    return {"id": ref, "tool": certified_tools.GET, "certified": True, "as_of": "2026-10-27",
            "figure": {"figure_id": "risk.annualized_volatility", "build_id": build, "value": 1.12}}


class RecorderTests(unittest.TestCase):
    def test_passes_results_through_unchanged_and_keeps_names_only(self):
        result = {"data": {"x": 1}, "figures_source": {"/data/periods/2/irr_annualized": "fallback",
                                                        "/data/periods/0/twr": "certified"}}
        inner = _Inner({"get_performance": result}, fail={"get_risk_metrics"})
        recorder = watch.Recorder(inner)
        self.assertIs(recorder.call("get_performance", {"account_id": "U1"}), result)
        with self.assertRaises(Exception):
            recorder.call("get_risk_metrics", {"account_id": "U1"})
        self.assertEqual(recorder.list_tools(), inner.list_tools())
        recorder.close()
        self.assertTrue(inner.closed)
        self.assertEqual(recorder.used, ["get_performance", "get_risk_metrics"])
        self.assertEqual(recorder.ok, ["get_performance"])
        self.assertEqual(recorder.failed, ["get_risk_metrics"])
        self.assertEqual(recorder.fallback, {"get_performance": 1})

    def test_keeps_the_freshness_dates_the_analyst_read(self):
        inner = _Inner({"get_data_freshness": {"data": {"latest_valuation_date": "2026-10-28",
                                                        "days_since_valuation": 1, "rows": 9}}})
        recorder = watch.Recorder(inner)
        recorder.call("get_data_freshness", {"account_id": "U1"})
        self.assertEqual(recorder.freshness,
                         {"U1": {"as_of": "2026-10-28", "source": "freshness", "lag_days": 1}})

    def test_fallback_marks_are_read_at_the_top_level_or_inside_data(self):
        self.assertEqual(watch.fallback_count({"data": {"figures_source": {"/a": "fallback", "/b": "fallback"}}}), 2)
        self.assertEqual(watch.fallback_count({"data": {}}), 0)
        self.assertEqual(watch.fallback_count({"figures_source": "fallback"}), 0)  # not the agreed map
        self.assertEqual(watch.fallback_count(None), 0)


class ModeTests(unittest.TestCase):
    def test_off_unless_exactly_watch_or_live(self):
        self.assertEqual(watch.mode({}), "off")
        self.assertEqual(watch.mode({"AGENT_SUPERVISOR": " Watch "}), "watch")
        self.assertEqual(watch.mode({"AGENT_SUPERVISOR": "live"}), "live")
        self.assertEqual(watch.mode({"AGENT_SUPERVISOR": "on"}), "off")
        self.assertEqual(watch.mode({"AGENT_SUPERVISOR": "Live!"}), "off")


class _TaskStore:
    def __init__(self, ready=True, accept=True, raises=False):
        self.ready, self.accept, self.raises = ready, accept, raises
        self.trees, self.ready_checks = [], 0

    def tasks_ready(self):
        self.ready_checks += 1
        return self.ready

    def add_tasks(self, rows):
        if self.raises:
            raise RuntimeError("database gone")
        self.trees.append(rows)
        return self.accept


def _freshness(as_of="2026-10-28", fail=False):
    def tools_for(user, correlation_id):
        return _Inner({"get_data_freshness": {"data": {"latest_valuation_date": as_of, "days_since_valuation": 1}}},
                      fail={"get_data_freshness"} if fail else ())
    return tools_for


class ChatTreeTests(unittest.TestCase):
    QUESTION = "How did I do this year, and what drove the risk? My secret note"

    def _tree(self, **kwargs):
        recorder = watch.Recorder(_Inner())
        for name in ("get_performance", "get_risk_metrics", certified_tools.GET):
            recorder.call(name, {"account_id": "U1"})
        turn = Turn(answer="Up 5% [T1], Sharpe 1.12 [F1].", tool_calls=4,
                    usage={"input_tokens": 100, "output_tokens": 20},
                    citations=[{"id": "T1", "tool": "get_performance", "as_of": "2026-10-27"},
                               _certified("F1", BUILD_A), _certified("F2", BUILD_B)])
        args = dict(route=route_chat(self.QUESTION), account_id="U1", correlation_id="corr-1",
                    conversation_id="c0ffee00-0000-0000-0000-000000000000", outcome="answer", turn=turn,
                    recorder=recorder, pinned={"as_of": "2026-10-28", "source": "freshness", "lag_days": 1},
                    elapsed_ms=1234)
        args.update(kwargs)
        return watch.chat_tree(**args)

    def test_one_tree_root_lead_specialists_and_checker(self):
        rows = self._tree()
        root, lead = rows[0], rows[1]
        self.assertEqual([r["level"] for r in rows], ["supervisor", "lead", "specialist", "specialist", "checker"])
        self.assertEqual([r["role"] for r in rows],
                         ["supervisor", "planner", "holdings_performance", "risk", "answer_checker"])
        self.assertTrue(all(r["root_id"] == root["task_id"] for r in rows))
        self.assertIsNone(root["parent_id"])
        self.assertEqual(lead["parent_id"], root["task_id"])
        self.assertTrue(all(r["parent_id"] == lead["task_id"] for r in rows[2:]))
        self.assertEqual(root["status"], "done")
        self.assertTrue(all(r["status"] == "shadow" for r in rows[1:]))
        self.assertTrue(all(r["mode"] == "watch" and r["source"] == "chat" for r in rows))
        self.assertTrue(all(re.match(r"^[a-z_]{1,40}$", r["role"]) for r in rows))

    def test_root_records_the_pin_the_builds_and_what_the_analyst_used(self):
        root = self._tree()[0]
        detail = root["detail"]
        self.assertEqual(root["pinned_as_of"], "2026-10-28")
        self.assertEqual(root["build_ids"], [BUILD_A, BUILD_B])
        self.assertTrue(detail["mixed_builds"])
        self.assertTrue(detail["behind_pin"])  # cited 27 Oct, pinned 28 Oct
        self.assertEqual(detail["agreement"], "agree")
        self.assertEqual(detail["used_specialists"], ["holdings_performance", "risk"])
        self.assertEqual(detail["layers"], {"silver": 2, "gold": 1})
        self.assertEqual((detail["tool_refs"], detail["figure_refs"]), (1, 2))
        self.assertEqual(root["usage"], {"input_tokens": 100, "output_tokens": 20, "tool_calls": 4})
        self.assertEqual(detail["pin"], {"source": "freshness", "lag_days": 1})

    def test_specialist_rows_carry_their_tool_lists_and_layers(self):
        risk = self._tree()[3]
        self.assertIn("get_risk_metrics", risk["tools_allowed"])
        self.assertIn(certified_tools.GET, risk["tools_allowed"])
        self.assertEqual(risk["tools_used"], ["get_risk_metrics"])
        self.assertEqual(risk["detail"]["layers"], ["code", "gold", "silver"])

    def test_nothing_from_the_question_or_the_answer_is_kept(self):
        text = json.dumps(self._tree(), default=str)
        for secret in ("secret note", "drove the risk", "Up 5%", "1.12"):
            self.assertNotIn(secret, text)
        for row in self._tree():
            self.assertLess(len(json.dumps(row.get("detail") or {})), 8000)

    def test_a_fast_path_has_no_checker_and_an_unplaced_question_goes_to_the_router(self):
        fast = self._tree(route=route_chat("What is my Sharpe ratio?"))
        self.assertEqual([r["role"] for r in fast], ["supervisor", "fast_path", "risk"])
        self.assertEqual(fast[0]["detail"]["agreement"], "under")
        unplaced = self._tree(route=route_chat("hello"), outcome="rejected")
        self.assertEqual([r["role"] for r in unplaced], ["supervisor", "router"])
        self.assertEqual((unplaced[0]["status"], unplaced[0]["detail"]["agreement"]), ("rejected", "undecided"))

    def test_an_answer_declined_as_advice_is_marked(self):
        from wealthryse_agent.runtime import OUT_OF_SCOPE

        rows = self._tree(route=route_chat("Should I sell Tesla?"), turn=Turn(answer=OUT_OF_SCOPE))
        self.assertTrue(rows[0]["detail"]["out_of_scope"])
        self.assertTrue(rows[0]["detail"]["route"]["advice"])
        self.assertNotIn("out_of_scope", self._tree()[0]["detail"])

    def test_a_failed_run_is_recorded_without_a_turn(self):
        rows = watch.chat_tree(route=route_chat("How did I do?"), account_id="U1", correlation_id="c",
                               conversation_id="c0ffee00-0000-0000-0000-000000000000", outcome="failed", turn=None,
                               recorder=None, pinned={"as_of": None, "source": "unavailable"}, elapsed_ms=5,
                               error_code="timeout")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["detail"]["error_code"], "timeout")
        self.assertEqual(rows[0]["usage"], {"tool_calls": 0})
        self.assertTrue(rows[0]["detail"]["usage_partial"])
        self.assertNotIn("usage_partial", self._tree()[0]["detail"])

    def test_a_failed_run_counts_the_calls_it_made(self):
        recorder = watch.Recorder(_Inner(fail={"get_risk_metrics"}))
        recorder.call("get_performance", {"account_id": "U1"})
        with self.assertRaises(Exception):
            recorder.call("get_risk_metrics", {"account_id": "U1"})
        rows = self._tree(outcome="failed", turn=None, recorder=recorder, error_code="timeout")
        self.assertEqual(rows[0]["usage"], {"tool_calls": 2})
        self.assertEqual(rows[0]["tools_used"], ["get_performance", "get_risk_metrics"])
        self.assertEqual(rows[0]["detail"]["tools_failed"], ["get_risk_metrics"])

    def test_a_failed_call_does_not_count_as_needed(self):
        recorder = watch.Recorder(_Inner(fail={"get_risk_metrics"}))
        recorder.call("get_performance", {"account_id": "U1"})
        with self.assertRaises(Exception):
            recorder.call("get_risk_metrics", {"account_id": "U1"})
        turn = Turn(answer="Up 5% [T1].", citations=[{"id": "T1", "tool": "get_performance", "as_of": "2026-10-28"}])
        detail = self._tree(turn=turn, recorder=recorder)[0]["detail"]
        self.assertEqual(detail["used_specialists"], ["holdings_performance"])
        self.assertEqual(detail["layers"], {"silver": 1})
        self.assertEqual(detail["agreement"], "over")

    def test_an_answer_from_certified_figures_alone_counts_their_specialist(self):
        recorder = watch.Recorder(_Inner())
        recorder.call(certified_tools.GET, {"account_id": "U1"})
        turn = Turn(answer="Volatility 12% [F1].", citations=[_certified("F1", BUILD_A)])
        detail = self._tree(route=route_chat("What is my volatility?"), turn=turn, recorder=recorder)[0]["detail"]
        self.assertEqual(detail["used_specialists"], ["risk"])
        self.assertEqual(detail["agreement"], "agree")

    def test_build_ids_stay_within_the_column_limit(self):
        citations = [_certified(f"F{i}", f"{i:08d}-0000-0000-0000-000000000000") for i in range(25)]
        root = self._tree(turn=Turn(answer="ok", citations=citations))[0]
        self.assertEqual(len(root["build_ids"]), 20)
        self.assertTrue(root["detail"]["mixed_builds"])


class RecordChatTests(unittest.TestCase):
    def _record(self, store, tools_for=None, table=None):
        return watch.record_chat(store, tools_for or _freshness(), user=USER, account_id="U1",
                                 question="How did I do?", correlation_id="corr",
                                 conversation_id="c0ffee00-0000-0000-0000-000000000000", outcome="answer",
                                 turn=Turn(answer="ok"), recorder=watch.Recorder(_Inner()), elapsed_ms=10,
                                 table=table or (lambda s: s.tasks_ready()))

    def test_writes_one_tree_with_the_pin(self):
        store = _TaskStore()
        self.assertTrue(self._record(store))
        self.assertEqual(len(store.trees), 1)
        self.assertEqual(store.trees[0][0]["pinned_as_of"], "2026-10-28")

    def test_does_nothing_until_the_table_exists(self):
        store = _TaskStore(ready=False)
        self.assertFalse(self._record(store))
        self.assertEqual(store.trees, [])

    def test_a_failed_pin_still_records_and_nothing_ever_raises(self):
        store = _TaskStore()
        self.assertTrue(self._record(store, tools_for=_freshness(fail=True)))
        self.assertEqual(store.trees[0][0]["detail"]["pin"]["source"], "unavailable")
        self.assertFalse(self._record(_TaskStore(raises=True)))
        self.assertFalse(self._record(_TaskStore(accept=False)))

        def no_client(user, correlation_id):
            raise RuntimeError("no token")

        self.assertTrue(self._record(_TaskStore(), tools_for=no_client))

    def test_the_pin_reuses_the_freshness_the_analyst_read(self):
        seen = []

        def tools_for(user, correlation_id):
            seen.append(correlation_id)
            return _freshness("2026-10-01")(user, correlation_id)

        recorder = watch.Recorder(_freshness()(USER, "corr"))
        recorder.call("get_data_freshness", {"account_id": "U1"})
        store = _TaskStore()
        self.assertTrue(watch.record_chat(store, tools_for, user=USER, account_id="U1", question="How did I do?",
                                          correlation_id="corr",
                                          conversation_id="c0ffee00-0000-0000-0000-000000000000",
                                          outcome="answer", turn=Turn(answer="ok"), recorder=recorder,
                                          table=lambda s: True))
        self.assertEqual(seen, [])
        self.assertEqual(store.trees[0][0]["pinned_as_of"], "2026-10-28")

    def test_trees_refused_in_a_row_pause_recording_until_the_next_check(self):
        now = [0.0]
        check = watch._TableCheck(clock=lambda: now[0])
        store = _TaskStore(accept=False)
        for _ in range(watch.REFUSALS_BEFORE_PAUSE):
            self.assertFalse(self._record(store, table=check))
        self.assertEqual(len(store.trees), watch.REFUSALS_BEFORE_PAUSE)
        self.assertFalse(self._record(store, table=check))  # paused: nothing sent
        self.assertEqual(len(store.trees), watch.REFUSALS_BEFORE_PAUSE)
        store.accept, now[0] = True, watch.TABLE_RECHECK_SECONDS + 1
        self.assertTrue(self._record(store, table=check))
        self.assertEqual(check.refusals, 0)

    def test_the_pin_uses_its_own_correlation_id(self):
        seen = []

        def tools_for(user, correlation_id):
            seen.append(correlation_id)
            return _freshness()(user, correlation_id)

        self._record(_TaskStore(), tools_for=tools_for)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], "corr")

    def test_a_missing_table_is_checked_again_only_after_ten_minutes(self):
        now = [0.0]
        check = watch._TableCheck(clock=lambda: now[0])
        store = _TaskStore(ready=False)
        self.assertFalse(check(store))
        self.assertFalse(check(store))
        self.assertEqual(store.ready_checks, 1)
        store.ready, now[0] = True, watch.TABLE_RECHECK_SECONDS + 1
        self.assertTrue(check(store))
        self.assertTrue(check(store))
        self.assertEqual(store.ready_checks, 2)


class _ChatStore:
    def __init__(self):
        self.saved = []

    def tokens_used_today(self):
        return 0

    def memories(self, user):
        return []

    def create_conversation(self, user, account_id, title):
        return "c0ffee00-0000-0000-0000-000000000000"

    def add_message(self, conversation_id, role, content, **kwargs):
        self.saved.append((role, content, kwargs.get("status", "ok")))
        return len(self.saved)


class ChatHookTests(unittest.TestCase):
    def _client(self, error=None, watch_raises=False):
        from fastapi.testclient import TestClient

        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings, ServiceAuthSettings

        store, runs, watched = _ChatStore(), [], []

        class Tools:
            def call(self, name, arguments):
                return {"data": {"accounts": [{"account_id": "U1"}]}}

            def list_tools(self):
                return []

            def close(self):
                pass

        class FakeAgent:
            settings = mock.Mock(chat_reasoning_mode="standard")

            def run(self, **kwargs):
                runs.append(kwargs)
                if error:
                    raise error
                kwargs["tools"].call("get_performance", {"account_id": "U1"})
                yield {"type": "status", "text": "Calculating returns"}
                yield {"type": "answer", "answer": "Up 5% [T1].", "citations": [], "turn": Turn(answer="Up 5% [T1].")}

        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 1000, 90), tenant_id="t",
                            store_factory=lambda: store, agent_factory=FakeAgent, tools_factory=lambda u, c: Tools())
        services._store = store
        services.keepalive_seconds = 0.05

        def watch_chat(**kwargs):
            watched.append(kwargs)
            if watch_raises:
                raise RuntimeError("can't start new thread")

        services.watch_chat = watch_chat
        return TestClient(create_app(services)), store, runs, watched

    def _ask(self, client):
        response = client.post("/v1/chat", json={"user_object_id": USER, "account_id": "U1", "message": "How did I do?"})
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_off_by_default_changes_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_SUPERVISOR", None)
            client, store, runs, watched = self._client()
            events = self._ask(client)
        self.assertEqual([e["type"] for e in events], ["start", "status", "answer", "done"])
        self.assertEqual(watched, [])
        self.assertIsInstance(runs[0]["tools"], guide_tools.GuideTools)

    def test_watch_records_after_the_answer_and_the_answer_is_the_same(self):
        with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR": "watch"}):
            client, store, runs, watched = self._client()
            events = self._ask(client)
        self.assertEqual([e["type"] for e in events], ["start", "status", "answer", "done"])
        self.assertEqual(store.saved, [("user", "How did I do?", "ok"), ("assistant", "Up 5% [T1].", "ok")])
        self.assertEqual(len(watched), 1)
        call = watched[0]
        self.assertEqual((call["outcome"], call["account_id"], call["question"]), ("answer", "U1", "How did I do?"))
        self.assertEqual(call["correlation_id"], events[0]["correlation_id"])
        self.assertIsInstance(runs[0]["tools"], watch.Recorder)
        self.assertEqual(call["recorder"].used, ["get_performance"])
        self.assertIsInstance(call["elapsed_ms"], int)

    def test_watch_records_a_failed_run(self):
        with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR": "watch"}):
            client, store, runs, watched = self._client(error=AgentError("timeout", "Too long."))
            events = self._ask(client)
        self.assertEqual([e["type"] for e in events], ["start", "error", "done"])
        self.assertEqual((watched[0]["outcome"], watched[0]["error_code"]), ("failed", "timeout"))

    def test_a_recorder_that_cannot_start_never_reaches_the_person(self):
        with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR": "watch"}):
            client, store, runs, watched = self._client(watch_raises=True)
            events = self._ask(client)
        self.assertEqual([e["type"] for e in events], ["start", "status", "answer", "done"])
        self.assertEqual(store.saved[-1], ("assistant", "Up 5% [T1].", "ok"))
        self.assertEqual(len(watched), 1)

    def test_watch_chat_runs_on_a_daemon_thread(self):
        done = []
        with mock.patch.object(watch, "record_chat", lambda **kwargs: done.append(kwargs)):
            thread = watch.watch_chat(user=USER)
            thread.join(2)
        self.assertTrue(thread.daemon)
        self.assertEqual(done, [{"user": USER}])


class ReportTests(unittest.TestCase):
    def test_summarises_routes_agreement_pins_and_cost(self):
        def root(lead, specialists, agreement_kind, tokens, **detail):
            return {"status": "done", "usage": {"input_tokens": tokens, "output_tokens": 0, "tool_calls": 2},
                    "detail": {"route": {"lead": lead, "specialists": specialists, "advice": False},
                               "agreement": agreement_kind, "used_specialists": specialists,
                               "layers": {"silver": 2}, "elapsed_ms": 1000, **detail}}

        roots = [root("fast_path", ["risk"], "agree", 100, figure_refs=1, mixed_builds=True),
                 root("planner", ["risk", "modelling"], "over", 300, tool_refs=2, behind_pin=True,
                      fallback={"get_performance": 1}),
                 root("router", [], "undecided", 50, pin={"source": "unavailable"}, out_of_scope=True)]
        failed = root("planner", ["risk", "modelling"], "over", 0, usage_partial=True)
        failed["status"] = "failed"
        self.assertEqual(report.summarise([*roots, failed])["cost"]["mean_tokens_by_lead"]["planner"], 300)
        summary = report.summarise(roots)
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["route"], {"fast_path": 1, "planner": 1, "router": 1, "advice": 0,
                                            "declined_as_advice": 1})
        self.assertEqual(summary["agreement"]["agree"], 1)
        self.assertEqual(summary["agreement"]["undecided"], 1)
        self.assertEqual(summary["specialists_planned"], {"risk": 2, "modelling": 1})
        self.assertEqual(summary["figures"]["answers_citing_certified"], 1)
        self.assertEqual(summary["figures"]["answers_citing_only_tools"], 1)
        self.assertEqual(summary["figures"]["with_fallback"], 1)
        self.assertEqual(summary["pin"], {"unavailable": 1, "behind_pin": 1, "mixed_builds": 1})
        self.assertEqual(summary["cost"]["mean_tokens_by_lead"], {"fast_path": 100, "planner": 300, "router": 50})
        self.assertEqual(summary["layers"], {"silver": 3})
        text = report.render(summary, "2026-10-29", "2026-11-09")
        self.assertIn("3 chat answers", text)
        self.assertIn("fast path 1 (33%)", text)

    def test_live_answers_are_counted_apart(self):
        watched = {"status": "done", "usage": {"input_tokens": 100, "output_tokens": 0, "tool_calls": 1},
                   "detail": {"route": {"lead": "fast_path", "specialists": ["risk"]}, "agreement": "agree"}}
        live_roots = [
            {"mode": "live", "status": "done", "usage": {"input_tokens": 400, "output_tokens": 100, "tool_calls": 3},
             "detail": {"route": {"lead": "planner", "specialists": ["risk", "modelling"]},
                        "plan": ["risk", "modelling"], "used_specialists": ["risk"], "objections": 1,
                        "revised": True, "elapsed_ms": 9000}},
            {"mode": "live", "status": "done", "usage": {"input_tokens": 90, "output_tokens": 10, "tool_calls": 1},
             "detail": {"route": {"lead": "fast_path", "specialists": ["risk"]}, "plan": ["risk"],
                        "used_specialists": ["risk"], "elapsed_ms": 3000, "figure_warnings": 1}},
        ]
        summary = report.summarise([{**watched, "mode": "watch"}, *live_roots])
        self.assertEqual(summary["requests"], 1, "the shadow comparison counts watched answers only")
        self.assertEqual(summary["cost"]["mean_tokens_by_lead"], {"fast_path": 100})
        team = summary["live"]
        self.assertEqual((team["requests"], team["lead"]), (2, {"planner": 1, "fast_path": 1}))
        self.assertEqual(team["checker"], {"objected": 1, "revised": 1})
        self.assertEqual(team["specialists_not_finished"], 1)
        self.assertEqual(team["cost"]["mean_tokens_by_lead"], {"planner": 500, "fast_path": 100})
        self.assertEqual(team["figure_check_warnings"], 1)
        text = report.render(summary, "2026-11-16", "2026-11-23")
        self.assertIn("Live answers from the client analysis team: 2, done 2.", text)
        self.assertIn("Checker objected to 1 (50%), rewritten 1", text)
        self.assertNotIn("Live answers", report.render(report.summarise([watched]), "2026-10-29", "2026-10-30"))

    def test_each_kiwoom_account_is_counted_apart(self):
        def root(subject, lead, agreement_kind, status="done", **detail):
            return {"mode": "watch", "status": status, "subject_id": subject, "usage": {},
                    "detail": {"route": {"lead": lead}, "agreement": agreement_kind, **detail}}

        roots = [root("KIWOOM-0000001111", "router", "undecided"),
                 root("KIWOOM-0000001111", "fast_path", "agree", figure_warnings=2),
                 root("KIWOOM-0000002222", "planner", "over", status="rejected"),
                 root("U1234567", "fast_path", "agree")]
        summary = report.summarise(roots)
        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["kiwoom"], {
            "KIWOOM-0000002222": {"requests": 1, "no_rule_matched": 0, "agree": 0, "figure_check_warnings": 0,
                                  "rejected": 1},
            "KIWOOM-0000001111": {"requests": 2, "no_rule_matched": 1, "agree": 1, "figure_check_warnings": 2,
                                  "rejected": 0}})
        text = report.render(summary, "2026-10-29", "2026-11-09")
        self.assertIn("Kiwoom account ending 1111: 2 answers, no rule matched 1 (50%), agree 1 (50%)", text)
        self.assertNotIn("0000001111", text, "the report shows only the last four digits")
        self.assertNotIn("Kiwoom", report.render(report.summarise(roots[3:]), "2026-10-29", "2026-11-09"))

    def test_an_empty_period(self):
        summary = report.summarise([])
        self.assertEqual(summary["requests"], 0)
        self.assertIn("0 chat answers", report.render(summary, "2026-10-29", "2026-10-30"))

    def test_the_command_refuses_a_backwards_period(self):
        with self.assertRaises(SystemExit):
            report.main(["--from", "2026-11-09", "--to", "2026-10-29"])


class BicepTests(unittest.TestCase):
    def test_the_supervisor_ships_off_and_only_on_the_chat_app(self):
        apps = (ROOT / "infra" / "agent-apps.bicep").read_text()
        self.assertIn("@allowed(['off', 'watch', 'live'])\nparam supervisorMode string = 'off'", apps)
        self.assertIn("param supervisorUsers string = ''", apps, "live mode names nobody by default")
        self.assertEqual(apps.count("name: 'AGENT_SUPERVISOR"), 2)
        chat_app = apps[apps.index("resource agent 'Microsoft.App/containerApps"):apps.index("// Container Apps job names")]
        self.assertIn("{ name: 'AGENT_SUPERVISOR', value: supervisorMode }", chat_app)
        self.assertIn("{ name: 'AGENT_SUPERVISOR_USERS', value: supervisorUsers }", chat_app)


class MigrationTests(unittest.TestCase):
    def test_the_task_table_is_additive_and_written_by_agent_writer_only(self):
        sql = (ROOT / "migrations" / "agent" / "018_supervisor_tasks.sql").read_text()
        code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        self.assertIn("CREATE TABLE IF NOT EXISTS agent.tasks", code)
        self.assertNotRegex(code, r"(?im)^\s*(DROP|ALTER|DELETE|UPDATE|TRUNCATE|INSERT)\b")
        self.assertNotIn("public.", code)
        self.assertIn("GRANT SELECT, INSERT ON agent.tasks TO agent_writer", code)
        self.assertNotIn("agent_reader", code)
        self.assertEqual(sorted(p.name for p in (ROOT / "migrations" / "agent").glob("018_*.sql")),
                         ["018_supervisor_tasks.sql"])
        # Every role the recorder writes passes the table's check.
        for member in registry.MEMBERS:
            self.assertRegex(member.name, r"^[a-z_]{1,40}$")
        for lead in ("fast_path", "planner", "router", "supervisor"):
            self.assertRegex(lead, r"^[a-z_]{1,40}$")
        statuses = re.search(r"status IN \(([^)]*)\)", code).group(1)
        self.assertIn("'shadow'", statuses)


if __name__ == "__main__":
    unittest.main()
