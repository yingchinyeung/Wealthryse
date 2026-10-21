"""The client analysis team in live mode: fast path, planner, specialists, writer, checker (no network).

The runtime is the real one; only the model and the tool server are scripted.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from wealthryse_agent import calculator, certified_tools  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.evaluate import DEFAULT_CASES  # noqa: E402
from wealthryse_agent.runtime import OUT_OF_SCOPE, UNVERIFIED, Agent, AgentError, Turn  # noqa: E402
from wealthryse_agent.supervisor import live, prompts, registry  # noqa: E402
from wealthryse_agent.supervisor.routing import agreement, route_chat  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402
from wealthryse_agent.tools import REGISTRY  # noqa: E402
from wealthryse_agent.validator import validate_answer  # noqa: E402

USER = "22222222-2222-2222-2222-222222222222"
CONVERSATION = "c0ffee00-0000-0000-0000-000000000000"
SETTINGS = ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="router",
                         region="southeastasia", processing_scope="apac-data-zone", managed_identity_client_id="x",
                         max_tool_calls=6, timeout_seconds=60)
RESULTS = {
    "get_performance": {"tool": "get_performance", "account_id": "U1", "as_of": "2026-10-28",
                        "data": {"periods": [{"key": "ytd", "twr": 0.0512}]}, "citations": []},
    "get_risk_metrics": {"tool": "get_risk_metrics", "account_id": "U1", "as_of": "2026-10-28",
                         "data": {"volatility": 0.1834}, "citations": []},
    "get_data_freshness": {"tool": "get_data_freshness", "account_id": "U1", "as_of": "2026-10-28",
                           "data": {"latest_valuation_date": "2026-10-28", "days_since_valuation": 0},
                           "citations": []},
    "project_wealth": {"tool": "project_wealth", "account_id": "U1", "as_of": "2026-10-28",
                       "data": {"median_value": 250000.0}, "citations": []},
}
# What each specialist calls first, and what it answers with.
SPECIALISTS = {
    "holdings_performance": ("get_performance", "Up 5.1% this year [T1]."),
    "risk": ("get_risk_metrics", "Volatility was 18.3% [T1]."),
    "modelling": ("project_wealth", "The median projection is 250,000 [T1]."),
}


def _usage(i=10, o=5):
    return SimpleNamespace(input_tokens=i, output_tokens=o)


def _json(name_or_value, usage=None):
    return SimpleNamespace(status="completed", output=[], usage=usage or _usage(), output_text=json.dumps(name_or_value))


def _call(name, call_id):
    arguments = json.dumps({"account_id": "U1"})
    return SimpleNamespace(type="function_call", name=name, arguments=arguments, call_id=call_id,
                           model_dump=lambda **_: {"type": "function_call", "name": name, "arguments": arguments,
                                                   "call_id": call_id})


def _answer(text, citations):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")],
                              model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
    return SimpleNamespace(status="completed", output=[message], usage=_usage(),
                           output_text=json.dumps({"answer": text, "citations": citations, "visuals": [], "remember": []}))


class _Model:
    """A scripted model client. Stateless per request, so specialists can run on several threads."""

    def __init__(self, *, in_scope=True, plan=None, objections=(), writer=("Up 5.1% [T1] with volatility 18.3% [T2].",),
                 fail=()):
        self.in_scope, self.plan, self.objections, self.writer, self.fail = in_scope, plan, list(objections), writer, fail
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self.create)

    def role(self, instructions):
        for name, area in prompts.AREAS.items():
            if instructions.startswith(area[:40]):
                return name
        return "writer" if "from what the specialists found" in instructions else "analyst"

    def create(self, **request):
        with self.lock:
            # A copy of the input: the runtime keeps adding to its conversation after the call.
            self.requests.append({**request, "input": list(request["input"]) if isinstance(request.get("input"), list)
                                  else request.get("input")})
        name = (request.get("text") or {}).get("format", {}).get("name")
        if request["model"] == "router":
            return _json({"in_scope": self.in_scope, "reason": "test"}, _usage(3, 1))
        if name == "plan":
            if self.plan == "garbage":
                return SimpleNamespace(status="completed", output=[], usage=_usage(), output_text="not json")
            return _json({"parts": self.plan or []})
        if name == "objections":
            objections, self.objections = (self.objections[:1], self.objections[1:])
            return _json({"objections": objections[0] if objections else []})
        role = self.role(request["instructions"])
        if role in self.fail:
            raise TimeoutError("model timed out")
        answered = any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in request["input"])
        if role == "writer":
            rewrite = "A checker read your first draft" in request["instructions"]
            text = self.writer[1] if rewrite and len(self.writer) > 1 else self.writer[0]
            return _answer(text, re.findall(r"\[([TF]\d+)\]", text))
        if role == "analyst":
            if not answered:
                return SimpleNamespace(status="completed", output=[_call("get_performance", "a1")], usage=_usage(),
                                       output_text="")
            return _answer("Up 5.1% this year [T1].", ["T1"])
        tool, text = SPECIALISTS[role]
        if not answered:
            return SimpleNamespace(status="completed", output=[_call(tool, role[:2] + "1")], usage=_usage(),
                                   output_text="")
        return _answer(text, ["T1"])

    def tools_offered(self, role):
        return [{t["name"] for t in r.get("tools") or []} for r in self.requests
                if r.get("instructions") and self.role(r["instructions"]) == role]


class _Tools:
    opened = 0

    def __init__(self, fail=()):
        self.fail = fail
        self.calls = []
        type(self).opened += 1

    def list_tools(self):
        return [{"name": n, "description": s.description, "inputSchema": s.parameters} for n, s in REGISTRY.items()]

    def call(self, name, arguments):
        self.calls.append(name)
        if name in self.fail:
            raise ToolCallFailed("tool_error", "The tool failed.")
        return dict(RESULTS[name])

    def close(self):
        pass


def _team(model, *, fail_tools=(), memory_block=""):
    agent = Agent(SETTINGS, model_client=model)
    return live.ClientTeam(agent, lambda user, correlation: _Tools(fail_tools), user=USER, account_id="U1",
                           correlation_id="corr-1", analyst_instructions="ANALYST INSTRUCTIONS",
                           memory_block=memory_block, today="2026-10-29")


def _final(events):
    return [e for e in events if e["type"] in ("answer", "rejected")][-1]


class SwitchTests(unittest.TestCase):
    def test_live_only_for_the_named_people(self):
        env = {"AGENT_SUPERVISOR": "live", "AGENT_SUPERVISOR_USERS": f" {USER.upper()} , someone-else"}
        self.assertTrue(live.live_for(USER, env))
        self.assertFalse(live.live_for("33333333-3333-3333-3333-333333333333", env))
        self.assertTrue(live.live_for("anyone", {"AGENT_SUPERVISOR": "live", "AGENT_SUPERVISOR_USERS": "*"}))
        self.assertFalse(live.live_for(USER, {"AGENT_SUPERVISOR": "live"}), "nobody is named by default")
        self.assertFalse(live.live_for(USER, {"AGENT_SUPERVISOR": "watch", "AGENT_SUPERVISOR_USERS": USER}))
        self.assertFalse(live.live_for(USER, {"AGENT_SUPERVISOR": "Live!", "AGENT_SUPERVISOR_USERS": USER}))


class EvidenceTests(unittest.TestCase):
    def test_a_run_can_start_from_earlier_results_and_continues_their_numbering(self):
        seeded = {"T1": RESULTS["get_performance"], "T2": RESULTS["get_risk_metrics"]}
        model = _ModelQueue([
            SimpleNamespace(status="completed", usage=_usage(), output_text="", output=[SimpleNamespace(
                type="function_call", name=calculator.NAME, call_id="k1",
                arguments=json.dumps({"expression": "0.0512 / 0.1834", "label": "return per unit of risk"}),
                model_dump=lambda **_: {"type": "function_call", "name": calculator.NAME, "call_id": "k1",
                                        "arguments": "{}"})]),
            _answer("Up 5.1% [T1] with volatility 18.3% [T2], about 0.28 per unit of risk [T3].", ["T1", "T2", "T3"])])
        events = list(Agent(SETTINGS, model_client=model).run(
            question="Return per unit of risk?", account_id="U1", tools=live._NoTools(),
            allowed_tools=[calculator.NAME], skip_router=True, evidence=(seeded, {})))
        final = _final(events)
        self.assertEqual(final["type"], "answer")
        self.assertEqual([c["id"] for c in final["turn"].citations], ["T1", "T2", "T3"])
        self.assertEqual(set(final["turn"].results), {"T1", "T2", "T3"})
        self.assertEqual(seeded.keys(), {"T1", "T2"}, "the caller's evidence is not changed")

    def test_merge_renumbers_each_specialists_refs(self):
        figure = {"ref": "F1", "figure_id": "risk.annualized_volatility", "value": 0.1834, "tool_call": "T1",
                  "lineage": {"build_id": "b1"}}
        risk = Turn(answer="Volatility was 18.3% [F1], from [T1].",
                    results={"T1": {"tool": certified_tools.GET, "figures": [figure]}}, figures={"F1": figure})
        perf = Turn(answer="Up 5.1% [T1]; cash 2% [T2].",
                    results={"T1": RESULTS["get_performance"], "T2": {"tool": "get_holdings", "data": {"cash": 0.02}}})
        results, figures, findings = live.merge([live.Step("holdings_performance", "done", turn=perf),
                                                 live.Step("risk", "done", turn=risk)])
        self.assertEqual(list(results), ["T1", "T2", "T3"])
        self.assertEqual(list(figures), ["F1"])
        self.assertEqual(findings["risk"], "Volatility was 18.3% [F1], from [T3].")
        self.assertEqual(figures["F1"]["tool_call"], "T3")
        self.assertEqual(results["T3"]["figures"][0]["ref"], "F1")
        self.assertEqual(figure["tool_call"], "T1", "the specialist's own record is not changed")
        check = validate_answer("Up 5.1% [T1] with volatility 18.3% [F1].", ["T1", "F1"], results, "", [], figures)
        self.assertTrue(check.ok, check.feedback())
        self.assertEqual(check.figure_checks, {"F1": "ok"})


class _ModelQueue:
    def __init__(self, responses):
        self.queue = list(responses)
        self.responses = SimpleNamespace(create=lambda **_: self.queue.pop(0))


class FastPathTests(unittest.TestCase):
    def test_one_specialist_answers_with_only_its_own_tools(self):
        model = _Model()
        team = _team(model, memory_block="\n\nPREFERENCES")
        events = list(team.answer("What is my volatility this year?"))
        final = _final(events)
        self.assertEqual(final["type"], "answer")
        self.assertEqual(final["answer"], "Volatility was 18.3% [T1].")
        self.assertEqual((team.lead, [s.name for s in team.steps]), ("fast_path", ["risk"]))
        offered = set().union(*model.tools_offered("risk"))
        self.assertIn("get_risk_metrics", offered)
        self.assertLessEqual(offered, set(registry.BY_NAME["risk"].tools))
        self.assertNotIn("get_holdings", offered)
        self.assertFalse(any((r.get("text") or {}).get("format", {}).get("name") == "plan" for r in model.requests))
        risk_instructions = [r["instructions"] for r in model.requests if r.get("instructions", "").startswith(
            prompts.AREAS["risk"][:40])][0]
        self.assertTrue(risk_instructions.endswith("PREFERENCES"))
        # Router 3+1, the specialist's two calls 10+5 each.
        self.assertEqual(final["turn"].usage, {"input_tokens": 23, "output_tokens": 11})
        self.assertEqual(final["turn"].tool_calls, 1)

        rows = team.tree(outcome="answer", turn=final["turn"], pinned={"as_of": "2026-10-28", "source": "freshness"},
                         conversation_id=CONVERSATION, elapsed_ms=900)
        self.assertEqual([r["role"] for r in rows], ["supervisor", "fast_path", "risk"])
        self.assertTrue(all(r["mode"] == "live" for r in rows))
        self.assertEqual([r["status"] for r in rows], ["done", "done", "done"])
        self.assertEqual(rows[2]["usage"], {"input_tokens": 20, "output_tokens": 10, "tool_calls": 1})
        self.assertEqual(rows[1]["usage"], {"input_tokens": 3, "output_tokens": 1, "tool_calls": 0})
        self.assertEqual(rows[0]["detail"]["agreement"], "agree")
        self.assertEqual(rows[0]["tools_used"], ["get_risk_metrics"])
        text = json.dumps(rows)
        for secret in ("volatility this year", "18.3", "PREFERENCES"):
            self.assertNotIn(secret, text)

    def test_out_of_scope_goes_no_further(self):
        model = _Model(in_scope=False)
        team = _team(model)
        events = list(team.answer("Should I sell my largest holding?"))
        self.assertEqual(_final(events)["answer"], OUT_OF_SCOPE)
        self.assertEqual((team.lead, team.steps, len(model.requests)), ("router", [], 1))
        rows = team.tree(outcome="answer", turn=_final(events)["turn"], pinned={"as_of": None},
                         conversation_id=CONVERSATION, elapsed_ms=5)
        self.assertEqual([r["role"] for r in rows], ["supervisor", "router"])
        self.assertTrue(rows[0]["detail"]["out_of_scope"])


class PlannerTests(unittest.TestCase):
    QUESTION = "How did I do this year, and how risky was it?"
    PLAN = [{"specialist": "holdings_performance", "question": "How did account U1 do this year?"},
            {"specialist": "risk", "question": "How risky was account U1 this year?"}]

    def test_specialists_run_in_parallel_and_one_answer_is_written_and_checked(self):
        model = _Model(plan=self.PLAN)
        team = _team(model)
        events = list(team.answer(self.QUESTION))
        final = _final(events)
        self.assertEqual(final["type"], "answer", final.get("reason"))
        self.assertEqual(final["answer"], "Up 5.1% [T1] with volatility 18.3% [T2].")
        self.assertEqual([(c["id"], c["tool"]) for c in final["turn"].citations],
                         [("T1", "get_performance"), ("T2", "get_risk_metrics")])
        self.assertIn({"type": "status", "text": "Asking specialists: holdings and performance, risk"}, events)
        self.assertEqual([(s.name, s.status) for s in team.steps], [("holdings_performance", "done"), ("risk", "done")])
        # Each specialist has half the request's tool calls.
        self.assertEqual(team._agent(share=2).settings.max_tool_calls, 3)
        writer = [r for r in model.requests if model.role(r.get("instructions", "")) == "writer"][0]
        self.assertIn("Volatility was 18.3% [T2].", writer["instructions"], "the risk findings are renumbered")
        self.assertEqual({t["name"] for t in writer["tools"]}, {calculator.NAME})
        # Router, planner, two specialists of two calls each, the writer and the checker.
        self.assertEqual(final["turn"].usage, {"input_tokens": 3 + 10 + 40 + 10 + 10, "output_tokens": 1 + 5 + 20 + 5 + 5})
        self.assertEqual(final["turn"].tool_calls, 2)

        rows = team.tree(outcome="answer", turn=final["turn"], pinned={"as_of": "2026-10-28"},
                         conversation_id=CONVERSATION, elapsed_ms=2000)
        self.assertEqual([r["role"] for r in rows],
                         ["supervisor", "planner", "holdings_performance", "risk", "answer_checker"])
        self.assertEqual(rows[0]["detail"]["plan"], ["holdings_performance", "risk"])
        self.assertEqual(rows[0]["detail"]["agreement"], "agree")
        self.assertEqual(rows[-1]["detail"], {"objections": 0})
        self.assertEqual(rows[0]["usage"]["input_tokens"], sum(r["usage"].get("input_tokens", 0) for r in rows[1:]))

    def test_an_objection_sends_the_answer_back_once(self):
        model = _Model(plan=self.PLAN, objections=[["The answer does not say how risky it was."]],
                       writer=("Up 5.1% [T1].", "Up 5.1% [T1], with volatility of 18.3% [T2]."))
        team = _team(model)
        final = _final(list(team.answer(self.QUESTION)))
        self.assertEqual(final["answer"], "Up 5.1% [T1], with volatility of 18.3% [T2].")
        self.assertEqual(team.checker["objections"], 1)
        self.assertTrue(team.checker["revised"])
        rewrite = [r for r in model.requests if "A checker read your first draft" in r.get("instructions", "")]
        self.assertEqual(len(rewrite), 1)
        self.assertIn("does not say how risky", rewrite[0]["instructions"])

    def test_a_figure_the_specialists_did_not_find_is_never_shown(self):
        model = _Model(plan=self.PLAN, writer=("Up 7.9% [T1].",))
        final = _final(list(_team(model).answer(self.QUESTION)))
        self.assertEqual((final["type"], final["answer"]), ("rejected", UNVERIFIED))

    def test_a_specialist_that_fails_is_named_and_the_rest_still_answer(self):
        model = _Model(plan=self.PLAN, fail=("risk",), writer=("Up 5.1% [T1]; the risk figures weren't available.",))
        team = _team(model)
        final = _final(list(team.answer(self.QUESTION)))
        self.assertEqual(final["type"], "answer")
        self.assertEqual([(s.name, s.status, s.error_code) for s in team.steps],
                         [("holdings_performance", "done", None), ("risk", "failed", "internal_error")])
        writer = [r for r in model.requests if model.role(r.get("instructions", "")) == "writer"][0]
        self.assertIn("This specialist could not finish its part.", writer["instructions"])
        rows = team.tree(outcome="answer", turn=final["turn"], pinned={}, conversation_id=CONVERSATION, elapsed_ms=1)
        self.assertEqual(rows[3]["status"], "failed")
        self.assertEqual(rows[3]["detail"]["error_code"], "internal_error")

    def test_nothing_is_shown_when_no_specialist_finished(self):
        model = _Model(plan=self.PLAN, fail=("risk", "holdings_performance"))
        team = _team(model)
        final = _final(list(team.answer(self.QUESTION)))
        self.assertEqual((final["type"], final["answer"]), ("rejected", live.NOTHING_FINISHED))
        self.assertFalse(any(model.role(r.get("instructions", "")) == "writer" for r in model.requests))

    def test_no_usable_plan_falls_back_to_todays_analyst(self):
        for plan in ("garbage", []):
            model = _Model(plan=plan)
            team = _team(model)
            final = _final(list(team.answer(self.QUESTION)))
            self.assertEqual(final["answer"], "Up 5.1% this year [T1].")
            self.assertEqual(team.lead, "analyst")
            analyst = [r for r in model.requests if r.get("instructions") == "ANALYST INSTRUCTIONS"]
            self.assertTrue(analyst)
            rows = team.tree(outcome="answer", turn=final["turn"], pinned={}, conversation_id=CONVERSATION,
                             elapsed_ms=1)
            self.assertEqual([r["role"] for r in rows], ["supervisor", "analyst"])
            self.assertEqual(rows[0]["detail"]["agreement"], "undecided")
            self.assertEqual(rows[0]["tools_used"], ["get_performance"], "the analyst's own tool calls are kept")

    def test_the_request_keeps_to_the_chat_time_limit(self):
        now = [0.0]
        model = _Model(plan=self.PLAN, objections=[["Something is unsupported."]])
        team = live.ClientTeam(Agent(SETTINGS, model_client=model), lambda u, c: _Tools(), user=USER, account_id="U1",
                               correlation_id="corr-1", analyst_instructions="A", today="2026-10-29",
                               clock=lambda: now[0])
        compose = team._compose

        def slow(*args, **kwargs):
            now[0] += 30  # the writer used half the request's 60 seconds
            return compose(*args, **kwargs)

        team._compose = slow
        final = _final(list(team.answer(self.QUESTION)))
        self.assertEqual(final["answer"], "Up 5.1% [T1] with volatility 18.3% [T2].")
        self.assertFalse(any((r.get("text") or {}).get("format", {}).get("name") == "objections" for r in model.requests),
                         "too little time left to check")
        rows = team.tree(outcome="answer", turn=final["turn"], pinned={}, conversation_id=CONVERSATION, elapsed_ms=1)
        self.assertNotIn("answer_checker", [r["role"] for r in rows])
        self.assertTrue(rows[0]["detail"]["checker_skipped"])
        now[0] = 50.0
        self.assertEqual(team._agent(writer=True).settings.timeout_seconds, 10)
        self.assertEqual(team._agent(share=2).settings.timeout_seconds, 10)

    def test_the_planner_and_checker_reason_less_than_the_chat(self):
        model = _Model(plan=self.PLAN)
        agent = Agent(dataclasses.replace(SETTINGS, reasoning_effort="xhigh"), model_client=model)
        team = live.ClientTeam(agent, lambda u, c: _Tools(), user=USER, account_id="U1", correlation_id="corr-1",
                               analyst_instructions="A", today="2026-10-29")
        list(team.answer(self.QUESTION))
        effort = {(r.get("text") or {}).get("format", {}).get("name"): r["reasoning"]["effort"]
                  for r in model.requests if r.get("reasoning")}
        self.assertEqual((effort["plan"], effort["objections"], effort["answer"]), ("low", "medium", "xhigh"))
        self.assertEqual(live._at_most("minimal", "medium"), "minimal")

    def test_chat_pro_mode_reaches_the_specialists_and_writer_but_not_the_planner_or_checker(self):
        model = _Model(plan=self.PLAN)
        agent = Agent(SETTINGS, model_client=model)
        team = live.ClientTeam(agent, lambda u, c: _Tools(), user=USER, account_id="U1", correlation_id="corr-1",
                               analyst_instructions="A", today="2026-10-29", reasoning_mode="pro")
        list(team.answer(self.QUESTION))
        modes = {}
        for r in model.requests:
            name = (r.get("text") or {}).get("format", {}).get("name")
            modes.setdefault(name, set()).add((r.get("reasoning") or {}).get("mode"))
        self.assertEqual(modes["answer"], {"pro"})  # every specialist and the writer
        self.assertEqual((modes["plan"], modes["objections"]), ({None}, {None}))
        agent.pro_refused = True
        self.assertTrue(team._agent(share=2).pro_refused)

    def test_a_plan_of_one_part_takes_the_fast_path_with_the_original_question(self):
        model = _Model(plan=[{"specialist": "modelling", "question": "Project account U1."},
                             {"specialist": "modelling", "question": "Again."}, {"specialist": "trading", "question": "x"}])
        team = _team(model)
        final = _final(list(team.answer("hello there")))
        self.assertEqual(final["answer"], "The median projection is 250,000 [T1].")
        self.assertEqual((team.lead, team.plan), ("planner", [("modelling", "Project account U1.")]))
        specialist = [r for r in model.requests if r.get("instructions", "").startswith(prompts.AREAS["modelling"][:40])][0]
        self.assertEqual(specialist["input"][-1], {"role": "user", "content": "hello there"})


class RecordTests(unittest.TestCase):
    class Store:
        def __init__(self):
            self.trees = []

        def tasks_ready(self):
            return True

        def add_tasks(self, rows):
            self.trees.append(rows)
            return True

    def test_the_pin_comes_from_the_specialists_freshness_reads_or_one_extra_call(self):
        model = _Model()
        team = _team(model)
        final = _final(list(team.answer("What is my volatility this year?")))
        team.steps[0].recorder.freshness["U1"] = {"as_of": "2026-10-28", "source": "freshness", "lag_days": 0}
        store = self.Store()
        self.assertTrue(live.record(store, team, outcome="answer", turn=final["turn"], conversation_id=CONVERSATION,
                                    table=lambda s: True))
        self.assertEqual(store.trees[0][0]["pinned_as_of"], "2026-10-28")
        team.steps[0].recorder.freshness.clear()
        opened = _Tools.opened
        self.assertTrue(live.record(store, team, outcome="answer", turn=final["turn"], conversation_id=CONVERSATION,
                                    table=lambda s: True))
        self.assertEqual(_Tools.opened, opened + 1)

    def test_recording_never_raises(self):
        team = _team(_Model())
        broken = SimpleNamespace(tasks_ready=lambda: True, add_tasks=lambda rows: 1 / 0)
        self.assertFalse(live.record(broken, team, outcome="failed", conversation_id=CONVERSATION,
                                     table=lambda s: True))
        rows = team.tree(outcome="failed", turn=None, pinned={}, conversation_id=CONVERSATION, elapsed_ms=1,
                         error_code="timeout")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertTrue(rows[0]["detail"]["usage_partial"])

    def test_every_role_the_team_records_fits_the_table(self):
        for role in ("supervisor", "fast_path", "planner", "analyst", "router", *registry.CLIENT_SPECIALISTS,
                     "answer_checker"):
            self.assertRegex(role, r"^[a-z_]{1,40}$")


class ChatTests(unittest.TestCase):
    def _client(self, model):
        from fastapi.testclient import TestClient

        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings, ServiceAuthSettings

        class Store:
            def __init__(self):
                self.saved = []

            def tokens_used_today(self):
                return 0

            def memories(self, user):
                return []

            def create_conversation(self, user, account_id, title):
                return CONVERSATION

            def add_message(self, conversation_id, role, content, **kwargs):
                self.saved.append((role, content, kwargs.get("status", "ok")))
                return len(self.saved)

        class Tools(_Tools):
            def call(self, name, arguments):
                if name == "list_accounts":
                    return {"data": {"accounts": [{"account_id": "U1"}]}}
                return super().call(name, arguments)

        store, recorded, watched = Store(), [], []
        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 1000, 90), tenant_id="t",
                            store_factory=lambda: store, agent_factory=lambda: Agent(SETTINGS, model_client=model),
                            tools_factory=lambda u, c: Tools())
        services._store = store
        services.keepalive_seconds = 0.05
        services.record_live = lambda **kwargs: recorded.append(kwargs)
        services.watch_chat = lambda **kwargs: watched.append(kwargs)
        return TestClient(create_app(services)), store, recorded, watched

    def _ask(self, client, user=USER, message="What is my volatility this year?"):
        response = client.post("/v1/chat", json={"user_object_id": user, "account_id": "U1", "message": message})
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_live_answers_the_named_people_and_watches_everyone_else(self):
        env = {"AGENT_SUPERVISOR": "live", "AGENT_SUPERVISOR_USERS": USER}
        with mock.patch.dict(os.environ, env):
            client, store, recorded, watched = self._client(_Model())
            events = self._ask(client)
            self.assertEqual([e["type"] for e in events][-2:], ["answer", "done"])
            self.assertEqual(store.saved[-1], ("assistant", "Volatility was 18.3% [T1].", "ok"))
            self.assertEqual(len(recorded), 1)
            self.assertEqual(recorded[0]["outcome"], "answer")
            self.assertEqual([s.name for s in recorded[0]["team"].steps], ["risk"])
            self.assertEqual(watched, [])

            client, store, recorded, watched = self._client(_Model())
            self._ask(client, user="33333333-3333-3333-3333-333333333333")
            self.assertEqual((len(recorded), len(watched)), (0, 1))

    def test_an_error_in_live_mode_reaches_the_person_and_is_recorded(self):
        env = {"AGENT_SUPERVISOR": "live", "AGENT_SUPERVISOR_USERS": "*"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(Agent, "in_scope", side_effect=AgentError("content_filtered", OUT_OF_SCOPE)):
            client, store, recorded, watched = self._client(_Model())
            events = self._ask(client)
        self.assertEqual([e["type"] for e in events], ["start", "error", "done"])
        self.assertEqual((recorded[0]["outcome"], recorded[0]["error_code"]), ("failed", "content_filtered"))

    def test_watch_mode_never_uses_the_team(self):
        with mock.patch.dict(os.environ, {"AGENT_SUPERVISOR": "watch", "AGENT_SUPERVISOR_USERS": "*"}):
            client, store, recorded, watched = self._client(_Model())
            self._ask(client)
        self.assertEqual((len(recorded), len(watched)), (0, 1))


class RoutingEvaluationTests(unittest.TestCase):
    """The routing rules against the analyst's evaluation questions (evaluate.DEFAULT_CASES)."""

    def test_the_rules_never_miss_a_specialist_the_question_needs(self):
        missed, fast = [], 0
        for case in DEFAULT_CASES:
            needed = sorted({registry.owner(t) for t in case.expected_tools} - {None})
            route = route_chat(case.question)
            fast += route.fast_path
            if needed and agreement(route.specialists, needed) not in ("agree", "over"):
                missed.append((case.case_id, list(route.specialists), needed))
        self.assertEqual(missed, [])
        self.assertGreaterEqual(fast / len(DEFAULT_CASES), 0.6, "most questions take the fast path")

    def test_advice_is_flagged_but_worry_is_not(self):
        self.assertTrue(route_chat("Rebalance my portfolio to 60/40.").advice)
        self.assertTrue(route_chat("Should I sell my largest holding?").advice)
        self.assertFalse(route_chat("How worried should I be about this account right now?").advice)
        self.assertEqual(route_chat("What is the excess return over the benchmark since inception?").specialists,
                         ("holdings_performance",))
        self.assertEqual(route_chat("What is a Sharpe ratio?").specialists, ("metrics_guide",))


class EvaluationRunTests(unittest.TestCase):
    def test_the_evaluation_run_can_ask_through_the_team(self):
        from wealthryse_agent import evaluate

        tools = _Tools()
        case = evaluate.Case("risk", "What is my volatility this year?", ("get_risk_metrics",))
        result = evaluate.run_case(Agent(SETTINGS, model_client=_Model()), tools, case, "U1", case.question,
                                   supervisor=True, user=USER)
        self.assertTrue(result.passed, result.reasons)
        self.assertEqual((result.via, result.tools), ("fast_path: risk", ["get_risk_metrics"]))
        planned = evaluate.run_case(Agent(SETTINGS, model_client=_Model(plan=PlannerTests.PLAN)), tools,
                                    evaluate.Case("both", PlannerTests.QUESTION, ("get_performance", "get_risk_metrics")),
                                    "U1", PlannerTests.QUESTION, supervisor=True, user=USER)
        self.assertTrue(planned.passed, planned.reasons)
        self.assertEqual(planned.via, "planner: holdings_performance, risk")
        self.assertIn("via: planner: holdings_performance, risk;", evaluate.summary([result, planned])[0])
        self.assertEqual(evaluate.run_case(Agent(SETTINGS, model_client=_Model()), tools, case, "U1",
                                           case.question).via, "", "without the option, today's analyst answers")


class PromptTests(unittest.TestCase):
    def test_each_specialist_is_told_about_its_own_tools_only(self):
        mentioned = {name for name in REGISTRY if name != "list_accounts"}
        for name in registry.CLIENT_SPECIALISTS:
            text = prompts.specialist(name, account_id="U1", today="2026-10-29", writes_answer=True)
            own = set(registry.BY_NAME[name].tools)
            for tool in mentioned - own:
                self.assertNotIn(tool, text, f"{name} is told about {tool}")
            self.assertIn("U1", text)

    def test_won_amounts_and_the_factor_fit_for_won_accounts(self):
        for name in registry.CLIENT_SPECIALISTS:
            text = prompts.specialist(name, account_id="KIWOOM-1", today="2026-10-29", writes_answer=False)
            self.assertIn("never in \ub9cc, \uc5b5 or \uc870", text)
        self.assertIn("never in \ub9cc, \uc5b5 or \uc870", prompts.writer(account_id="U1", today="2026-10-29", findings={}, failed=[]))
        for name in ("risk", "modelling"):
            self.assertIn("no Korea set", prompts.AREAS[name])
        self.assertIn("matched nothing", prompts.AREAS["modelling"])

    def test_the_writer_keeps_braces_in_findings(self):
        text = prompts.writer(account_id="U1", today="2026-10-29", findings={"risk": "a {weird} finding"}, failed=[])
        self.assertIn("a {weird} finding", text)
        self.assertIn("The current account is U1. Today is 2026-10-29.", text)


if __name__ == "__main__":
    unittest.main()
