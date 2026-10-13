"""Unit tests for the wealthryse_agent package (no database or network)."""
from __future__ import annotations

import json
import os
import unittest
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from wealthryse_agent.config import ModelSettings, ServiceAuthSettings  # noqa: E402
from wealthryse_agent.jobs import data_quality_findings  # noqa: E402
from wealthryse_agent.runtime import OUT_OF_SCOPE, UNVERIFIED, Agent, AgentError  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402
from wealthryse_agent.tools import REGISTRY, ToolError, validate_arguments  # noqa: E402
from wealthryse_agent.validator import validate_answer  # noqa: E402
from wealthryse_agent import auth, evaluate  # noqa: E402

RESULTS = {
    "T1": {"tool": "get_performance", "account_id": "U1", "as_of": "2026-03-31",
           "data": {"currency": "USD", "latest_nav": 12705.0,
                    "periods": [{"key": "ytd", "twr": 0.16263636, "net_flows": 1000.0, "end_date": "2026-03-31"}]}},
    "T2": {"tool": "compare_to_benchmark", "account_id": "U1", "as_of": "2026-03-31",
           "data": {"periods": [{"portfolio_return": 0.16263636, "benchmark_return": 0.05, "excess_return": 0.11263636}]}},
    "T3": {"tool": "get_holdings_changes", "account_id": "U1", "as_of": "2026-03-31",
           "data": {"from_fx": -40.0, "total_change": 1234567.0}},
}


class ValidatorTests(unittest.TestCase):
    def test_rounded_figures_from_tools_pass(self):
        answer = ("The account returned 16.3% this year against 5% for the benchmark, "
                  "11.26% ahead [T1][T2]. NAV is $12,705 and $1.2m changed hands [T3].")
        self.assertTrue(validate_answer(answer, ["T1", "T2", "T3"], RESULTS).ok)

    def test_invented_figure_is_rejected(self):
        check = validate_answer("The account returned 17.1% this year [T1].", ["T1"], RESULTS)
        self.assertFalse(check.ok)
        self.assertEqual(check.unverified, ["17.1%"])

    def test_wrong_sign_is_rejected(self):
        self.assertFalse(validate_answer("Currency moves added +40 [T3].", ["T3"], RESULTS).ok)
        self.assertTrue(validate_answer("Currency moves cost -40 [T3].", ["T3"], RESULTS).ok)

    def test_unknown_citation_is_rejected(self):
        check = validate_answer("Returns were 5% [T9].", ["T9"], RESULTS)
        self.assertEqual(check.bad_citations, ["T9"])

    def test_figures_need_a_citation(self):
        self.assertFalse(validate_answer("Returns were 5%.", [], RESULTS).ok)

    def test_counts_dates_and_question_numbers_are_allowed(self):
        answer = "As of 2026-03-31 your top 3 holdings drove 2026's gains [T1]; you asked about 42."
        self.assertTrue(validate_answer(answer, ["T1"], RESULTS, question="What about 42?").ok)

    def test_unknown_date_is_rejected(self):
        self.assertEqual(validate_answer("On 2026-04-01 it rose [T1].", ["T1"], RESULTS).bad_dates, ["2026-04-01"])

    def test_account_ids_with_digits_are_not_figures(self):
        self.assertTrue(validate_answer("Account U16088571 is covered [T1].", ["T1"], RESULTS).ok)


class ToolArgumentTests(unittest.TestCase):
    def test_unexpected_and_missing_arguments(self):
        with self.assertRaises(ToolError):
            validate_arguments(REGISTRY["get_holdings"], {"account_id": "U1", "sql": "select 1"})
        with self.assertRaises(ToolError):
            validate_arguments(REGISTRY["get_holdings_changes"], {"account_id": "U1"})
        with self.assertRaises(ToolError):
            validate_arguments(REGISTRY["get_holdings"], {"account_id": "U1", "limit": True})

    def test_every_tool_is_read_only_and_closed(self):
        for spec in REGISTRY.values():
            self.assertFalse(spec.parameters["additionalProperties"], spec.name)
            self.assertNotIn("write", spec.name)


class _FakeTools:
    def __init__(self, results=None, fail=None):
        self.results = results or {}
        self.fail = fail or {}
        self.calls = []

    def list_tools(self):
        return [{"name": n, "description": s.description, "inputSchema": s.parameters} for n, s in REGISTRY.items()]

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name in self.fail:
            raise ToolCallFailed(*self.fail[name])
        return self.results[name]


def _call(name, arguments, call_id="c1"):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id,
                           model_dump=lambda **_: {"type": "function_call", "name": name,
                                                   "arguments": json.dumps(arguments), "call_id": call_id})


def _final(answer, citations):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text")],
                              model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
    return SimpleNamespace(status="completed", output=[message], usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                           output_text=json.dumps({"answer": answer, "citations": citations}))


def _step(*calls):
    return SimpleNamespace(status="completed", output=list(calls), usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                           output_text="")


class _FakeModel:
    def __init__(self, responses):
        self.responses = SimpleNamespace(create=self._create)
        self._queue = list(responses)
        self.requests = []

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self._queue.pop(0)


SETTINGS = ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                         region="southeastasia", processing_scope="apac-data-zone", managed_identity_client_id="x",
                         max_tool_calls=3)
PERF = {"tool": "get_performance", "account_id": "U1", "as_of": "2026-03-31", "data": {"periods": [{"twr": 0.0512}]},
        "citations": [{"source": "subject_nav_series"}]}


class RuntimeTests(unittest.TestCase):
    def _run(self, model, tools, **kwargs):
        return list(Agent(SETTINGS, model_client=model).run(question="How did I do?", account_id="U1", tools=tools, **kwargs))

    def test_tool_loop_then_validated_answer(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U1"})), _final("Up 5.1% [T1].", ["T1"])])
        tools = _FakeTools({"get_performance": PERF})
        events = self._run(model, tools)
        self.assertEqual([e["type"] for e in events], ["status", "answer"])
        self.assertEqual(events[-1]["citations"][0]["tool"], "get_performance")
        self.assertEqual(events[-1]["turn"].usage, {"input_tokens": 20, "output_tokens": 10})
        self.assertFalse(model.requests[0]["store"])
        output = [i for i in model.requests[1]["input"] if i.get("type") == "function_call_output"][0]
        self.assertEqual(json.loads(output["output"])["citation_id"], "T1")

    def test_invented_figure_retries_once_then_rejects(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U1"})),
                            _final("Up 9% [T1].", ["T1"]), _final("Up 9.9% [T1].", ["T1"])])
        events = self._run(model, _FakeTools({"get_performance": PERF}))
        self.assertEqual(events[-1]["type"], "rejected")
        self.assertEqual(events[-1]["answer"], UNVERIFIED)

    def test_invented_figure_fixed_on_retry(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U1"})),
                            _final("Up 9% [T1].", ["T1"]), _final("Up 5.12% [T1].", ["T1"])])
        self.assertEqual(self._run(model, _FakeTools({"get_performance": PERF}))[-1]["type"], "answer")

    def test_tool_errors_go_back_to_the_model_not_the_person(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U2"})),
                            _final("I can't see that account.", [])])
        tools = _FakeTools(fail={"get_performance": ("account_not_allowed", "You do not have access to that account.")})
        events = self._run(model, tools)
        self.assertEqual(events[-1]["answer"], "I can't see that account.")
        output = [i for i in model.requests[1]["input"] if i.get("type") == "function_call_output"][0]
        self.assertEqual(json.loads(output["output"])["error"]["code"], "account_not_allowed")

    def test_tools_outside_the_allowed_list_are_not_offered_or_run(self):
        model = _FakeModel([_step(_call("get_cash_flows", {"account_id": "U1"})), _final("No.", [])])
        tools = _FakeTools()
        self._run(model, tools, allowed_tools=["get_performance"])
        self.assertEqual([t["name"] for t in model.requests[0]["tools"]], ["get_performance"])
        self.assertEqual(tools.calls, [])

    def test_step_cap(self):
        calls = [_step(_call("get_performance", {"account_id": "U1"}, f"c{i}")) for i in range(5)]
        with self.assertRaises(AgentError) as caught:
            self._run(_FakeModel(calls), _FakeTools({"get_performance": PERF}))
        self.assertEqual(caught.exception.code, "too_many_steps")

    def test_router_blocks_out_of_scope(self):
        settings = ModelSettings(**{**SETTINGS.__dict__, "router_deployment": "router"})
        model = _FakeModel([SimpleNamespace(output_text=json.dumps({"in_scope": False, "reason": "trade"}), usage=None)])
        events = list(Agent(settings, model_client=model).run(question="Buy 100 AAPL", account_id="U1", tools=_FakeTools()))
        self.assertEqual(events[-1]["answer"], OUT_OF_SCOPE)

    def test_region_is_enforced(self):
        with self.assertRaises(ValueError):
            Agent(ModelSettings(**{**SETTINGS.__dict__, "region": "eastus"}), model_client=object())


class EvaluationTests(unittest.TestCase):
    def _run(self, case, model, tools, subject="U1"):
        return evaluate.run_case(Agent(SETTINGS, model_client=model), tools, case, subject, case.question)

    def test_in_scope_answer_with_expected_tool_passes(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U1"})), _final("Up 5.1% [T1].", ["T1"])])
        result = self._run(evaluate.Case("p", "How did I do?", ("get_performance",)), model,
                           _FakeTools({"get_performance": PERF}))
        self.assertTrue(result.passed, result.reasons)
        self.assertEqual(result.tools, ["get_performance"])
        self.assertEqual(result.tokens, 30)

    def test_missing_tool_and_rejection_fail(self):
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U1"})),
                            _final("Up 9% [T1].", ["T1"]), _final("Up 9.9% [T1].", ["T1"])])
        result = self._run(evaluate.Case("p", "How did I do?", ("compare_to_benchmark",)), model,
                           _FakeTools({"get_performance": PERF}))
        self.assertFalse(result.passed)
        self.assertIn("answer rejected by the figure check", result.reasons)
        self.assertIn("did not use compare_to_benchmark", result.reasons)

    def test_another_account_fails_even_when_allowed(self):
        other = {**PERF, "account_id": "U2"}
        model = _FakeModel([_step(_call("get_performance", {"account_id": "U2"})), _final("Up 5.1% [T1].", ["T1"])])
        result = self._run(evaluate.Case("x", "And U2?", must_refuse=True), model, _FakeTools({"get_performance": other}))
        self.assertIn("tool call for another account", result.reasons)
        self.assertIn("citation from another account", result.reasons)
        self.assertIn("answered with data instead of refusing", result.reasons)

    def test_refusal_passes_and_errors_fail(self):
        refused = self._run(evaluate.Case("r", "Sell everything", must_refuse=True),
                            _FakeModel([_final("I can't advise on trades.", [])]), _FakeTools())
        self.assertTrue(refused.passed, refused.reasons)
        calls = [_step(_call("get_performance", {"account_id": "U1"}, f"c{i}")) for i in range(5)]
        failed = self._run(evaluate.Case("s", "q"), _FakeModel(calls), _FakeTools({"get_performance": PERF}))
        self.assertEqual(failed.reasons, ["error too_many_steps"])

    def test_cases_spread_over_accounts_and_skip_cross_account_with_one(self):
        cases = [evaluate.Case("a", "q"), evaluate.Case("b", "Show {other_account}", must_refuse=True),
                 evaluate.Case("c", "q", subject_id="U9")]
        self.assertEqual(evaluate.assign_cases(cases, ["U1", "U2"]),
                         [(cases[0], "U1", "q"), (cases[1], "U2", "Show U1"), (cases[2], "U9", "q")])
        self.assertEqual([c.case_id for c, _, _ in evaluate.assign_cases(cases, ["U1"])], ["a", "c"])

    def test_default_set_and_readiness(self):
        ids = [c.case_id for c in evaluate.DEFAULT_CASES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 50)
        names = set(REGISTRY)
        self.assertTrue(all(set(c.expected_tools) <= names for c in evaluate.DEFAULT_CASES))
        good = [evaluate.Result(str(i), "U1", True) for i in range(10)]
        self.assertTrue(evaluate.summary(good)[1])
        bad = good[:9] + [evaluate.Result("x", "U1", False, ["tool call for another account"])]
        self.assertFalse(evaluate.summary(bad)[1])


class AuthTests(unittest.TestCase):
    def test_unconfigured_service_refuses(self):
        with self.assertRaises(auth.Unauthorized):
            auth.verify_service_token("Bearer x", ServiceAuthSettings("", "", frozenset()))

    def test_missing_token_refuses(self):
        settings = ServiceAuthSettings("t", "api://x", frozenset({"a"}))
        with self.assertRaises(auth.Unauthorized):
            auth.verify_service_token(None, settings)

    def test_key_fetch_gives_up_before_callers_do(self):
        self.assertLess(auth._jwks("tenant-for-timeout-test").timeout, 30)


class ToolClientReadinessTests(unittest.TestCase):
    def _client(self, handler):
        import httpx
        from wealthryse_agent.tool_client import ToolClient

        tokens = SimpleNamespace(header=lambda: {"Authorization": "Bearer test"})
        return ToolClient("https://tools.internal", tokens, job="data_quality", correlation_id="c1",
                          transport=httpx.MockTransport(handler))

    def test_waits_for_a_cold_tool_server(self):
        import httpx

        calls, pauses = [], []

        def handler(request):
            calls.append(json.loads(request.content)["method"])
            if len(calls) < 3:
                raise httpx.ReadTimeout("cold start", request=request)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        self._client(handler).wait_until_ready(sleep=pauses.append)
        self.assertEqual(calls, ["ping", "ping", "ping"])
        self.assertEqual(len(pauses), 2)

    def test_gives_up_after_the_last_attempt(self):
        import httpx

        def handler(request):
            return httpx.Response(503)

        with self.assertRaises(httpx.HTTPStatusError):
            self._client(handler).wait_until_ready(attempts=3, sleep=lambda _: None)

    def test_does_not_retry_a_refused_caller(self):
        import httpx

        pauses = []
        with self.assertRaises(ToolCallFailed):
            self._client(lambda request: httpx.Response(403)).wait_until_ready(sleep=pauses.append)
        self.assertEqual(pauses, [])


class DataQualityTests(unittest.TestCase):
    def test_findings(self):
        found = data_quality_findings({"latest_valuation_date": "2026-10-01", "latest_holdings_date": "2026-09-30",
                                       "benchmarks": [{"benchmark_id": "GSPC", "latest": "2026-09-01"}]}, date(2026, 10, 12))
        self.assertEqual(len(found), 3)
        self.assertEqual(data_quality_findings({"latest_valuation_date": "2026-10-10", "latest_holdings_date": "2026-10-10",
                                                "benchmarks": []}, date(2026, 10, 12)), [])


if __name__ == "__main__":
    unittest.main()


class _FakeStore:
    def __init__(self):
        self.messages_saved = []
        self.conversations_made = []

    def tokens_used_today(self):
        return 0

    def create_conversation(self, user, account_id, title):
        self.conversations_made.append((user, account_id))
        return "c0ffee00-0000-0000-0000-000000000000"

    def add_message(self, conversation_id, role, content, **kwargs):
        self.messages_saved.append((role, content, kwargs.get("status", "ok")))
        return len(self.messages_saved)


class AgentServerTests(unittest.TestCase):
    def _client(self, store, events, accounts=("U1",)):
        from fastapi.testclient import TestClient

        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings
        from wealthryse_agent.runtime import Turn

        class Tools:
            def call(self, name, arguments):
                return {"data": {"accounts": [{"account_id": a} for a in accounts]}}

            def close(self):
                pass

        class FakeAgent:
            def run(self, **kwargs):
                for event in events:
                    if event["type"] in ("answer", "rejected"):
                        event = {**event, "turn": Turn(answer=event["answer"], status="ok")}
                    yield event

        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 1000, 90), tenant_id="t",
                            store_factory=lambda: store, agent_factory=FakeAgent, tools_factory=lambda u, c: Tools())
        services._store = store  # bypass the connection lock wrapper in tests
        return TestClient(create_app(services))

    def test_chat_streams_and_saves_only_validated_answer(self):
        store = _FakeStore()
        client = self._client(store, [{"type": "status", "text": "Reading holdings"},
                                      {"type": "answer", "answer": "Up 5% [T1].", "citations": []}])
        response = client.post("/v1/chat", json={"user_object_id": "22222222-2222-2222-2222-222222222222",
                                                 "account_id": "U1", "message": "How did I do?"})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual([e["type"] for e in events], ["start", "status", "answer", "done"])
        self.assertEqual(store.messages_saved, [("user", "How did I do?", "ok"), ("assistant", "Up 5% [T1].", "ok")])

    def test_chat_refuses_accounts_the_person_cannot_see(self):
        store = _FakeStore()
        client = self._client(store, [], accounts=("U2",))
        response = client.post("/v1/chat", json={"user_object_id": "22222222-2222-2222-2222-222222222222",
                                                 "account_id": "U1", "message": "hi"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(store.conversations_made, [])
