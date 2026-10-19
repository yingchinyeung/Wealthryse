"""Metrics guide agents: definitions as tools, Explain with your numbers and Ask the guide."""
from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ibkr_etl import agent_web  # noqa: E402
from ibkr_etl import metrics_guide as guide  # noqa: E402
from wealthryse_agent import guide_tools  # noqa: E402
from wealthryse_agent.config import ModelSettings, RuntimeSettings, ServiceAuthSettings  # noqa: E402
from wealthryse_agent.runtime import TOOL_LABELS, Agent, Turn  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402
from wealthryse_agent.tools import REGISTRY  # noqa: E402
from wealthryse_agent.validator import validate_answer  # noqa: E402

USER = "22222222-2222-2222-2222-222222222222"
OWNER = {"object_id": "11111111-1111-1111-1111-111111111111", "email": "o@example.invalid", "role": "owner"}


class GuideSearchTest(unittest.TestCase):
    def test_search_finds_definitions_from_plain_words_and_aliases(self):
        self.assertEqual(guide.search("how is CVaR calculated")[0]["name"], "Daily Expected Tail Loss (95%)")
        self.assertEqual(guide.search("what is my sharpe ratio")[0]["name"], "Sharpe ratio")
        self.assertIn("Effective number of holdings", [r["name"] for r in guide.search("how concentrated am I")])
        self.assertEqual(guide.search("why did my risk go up")[0]["name"], "Annualized volatility")
        self.assertEqual(guide.search("zzzz"), [])

    def test_payload_carries_the_link_location_and_the_numbers_the_wording_quotes(self):
        payload = guide.definition_payload("Shock half-life")
        self.assertEqual(payload["link"], "/metrics-guide#metric-shock-half-life")
        self.assertEqual(payload["where_you_see_it"], "Modelling › Risk outlook")
        self.assertIn(252.0, guide.definition_payload("Sharpe ratio")["figures_in_text"])
        self.assertIn(2000.0, guide.definition_payload("Wealth projection range")["figures_in_text"])


class GuideToolsTest(unittest.TestCase):
    def test_every_section_maps_to_real_account_tools(self):
        self.assertEqual(set(guide_tools.SECTION_TOOLS), {s.key for s in guide.METRIC_SECTIONS})
        for names in guide_tools.SECTION_TOOLS.values():
            for name in names:
                self.assertIn(name, REGISTRY)
                self.assertFalse(REGISTRY[name].only_jobs, name)
        self.assertEqual(guide_tools.tools_for_metric("Forecast volatility"),
                         ("project_wealth", "get_risk_outlook", "get_market_regimes"))
        for name in guide_tools.NAMES:
            self.assertIn(name, TOOL_LABELS)

    def test_definition_lookup_search_and_errors(self):
        result = guide_tools.call("get_metric_definition", {"name": "Sharpe ratio"})
        self.assertEqual(result["data"]["account_tools"], ["get_risk_metrics", "get_nav_history", "run_stress_test"])
        self.assertEqual(result["citations"][0]["link"], "/metrics-guide#metric-sharpe-ratio")
        with self.assertRaises(ToolCallFailed) as error:
            guide_tools.call("get_metric_definition", {"name": "Sharp ratio"})
        self.assertIn("Sharpe ratio", error.exception.message)
        found = guide_tools.call("search_metrics_guide", {"query": "regime", "limit": 2})
        self.assertEqual([d["name"] for d in found["data"]["definitions"]], ["Market regime", "Regime outlook range"])
        with self.assertRaises(ToolCallFailed):
            guide_tools.call("search_metrics_guide", {"query": "x" * 201})

    def test_wrapper_adds_guide_tools_and_passes_account_tools_through(self):
        inner = mock.Mock()
        inner.list_tools.return_value = [{"name": "get_risk_metrics", "description": "", "inputSchema": {}}]
        inner.call.return_value = {"data": 1}
        tools = guide_tools.GuideTools(inner)
        self.assertEqual([t["name"] for t in tools.list_tools()],
                         ["get_risk_metrics", "get_metric_definition", "search_metrics_guide"])
        self.assertEqual(tools.call("get_risk_metrics", {"account_id": "U1"}), {"data": 1})
        self.assertEqual(tools.call("get_metric_definition", {"name": "Beta"})["tool"], "get_metric_definition")
        tools.close()
        inner.close.assert_called_once()
        with self.assertRaises(ToolCallFailed):
            guide_tools.GuideTools().call("get_risk_metrics", {})

    def test_guide_links_come_only_from_guide_tool_citations(self):
        citations = [{"tool": "get_metric_definition", "sources": [{"source": "metrics_guide", "metric": "Beta"}]},
                     {"tool": "get_risk_metrics", "sources": [{"source": "metrics_guide", "metric": "Correlation"}]},
                     {"tool": "search_metrics_guide", "sources": [{"source": "metrics_guide", "metric": "Beta"},
                                                                  {"source": "metrics_guide", "metric": "nope"}]}]
        self.assertEqual(guide_tools.guide_links(citations), [{"name": "Beta", "link": "/metrics-guide#metric-beta"}])


def _call(name, arguments, call_id):
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id,
                           model_dump=lambda **_: {"type": "function_call", "name": name,
                                                   "arguments": json.dumps(arguments), "call_id": call_id})


def _response(output, text=""):
    return SimpleNamespace(status="completed", output=output, output_text=text,
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class WorkedExampleTest(unittest.TestCase):
    """A real Agent run: the guide's constants and the account's figures pass the figure check."""

    def test_annualising_volatility_with_the_guides_252_is_accepted_and_an_invented_number_is_not(self):
        risk = {"tool": "get_risk_metrics", "account_id": "U1", "as_of": "2026-10-15",
                "data": {"daily_volatility": 0.01, "annualized_volatility": 0.1587},
                "citations": [{"source": "subject_nav_series"}]}
        inner = mock.Mock()
        inner.list_tools.return_value = [{"name": "get_risk_metrics", "description": "", "inputSchema": {}}]
        inner.call.return_value = risk
        message = SimpleNamespace(type="message", content=[], model_dump=lambda **_: {"type": "message", "role": "assistant", "content": []})
        good = json.dumps({"answer": "Volatility is 15.87% a year [T3]: 0.01 a day times the square root of 252 "
                                     "[T1][T2]. See [Annualized volatility](/metrics-guide#metric-annualized-volatility).",
                           "citations": ["T1", "T2", "T3"], "visuals": [], "remember": []})
        queue = [
            _response([_call("get_metric_definition", {"name": "Annualized volatility"}, "a"),
                       _call("get_risk_metrics", {"account_id": "U1"}, "b")]),
            _response([_call("calculate", {"expression": "0.01 * sqrt(252)"}, "c")]),
            _response([message], good),
        ]
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: queue.pop(0)))
        settings = ModelSettings(endpoint="https://example.invalid", deployment="analyst", router_deployment="",
                                 region="southeastasia", processing_scope="apac-data-zone",
                                 managed_identity_client_id="x", max_tool_calls=6)
        events = list(Agent(settings, model_client=client).run(
            question="Work it through", account_id="U1", tools=guide_tools.GuideTools(inner), skip_router=True,
            allowed_tools=["get_metric_definition", "get_risk_metrics", "calculate"]))
        answer = events[-1]
        self.assertEqual(answer["type"], "answer", answer)
        self.assertEqual(guide_tools.guide_links(answer["citations"]),
                         [{"name": "Annualized volatility", "link": "/metrics-guide#metric-annualized-volatility"}])
        results = {"T1": guide_tools.call("get_metric_definition", {"name": "Annualized volatility"}), "T2": risk}
        self.assertFalse(validate_answer("Volatility is 0.01 a day times the square root of 365 [T1].", ["T1"], results).ok)


class _Store:
    def __init__(self):
        self.conversations, self.messages, self.runs = [], [], []

    def tokens_used_today(self):
        return 0

    def memories(self, user):
        return []

    def create_conversation(self, user, subject, title):
        self.conversations.append((subject, title))
        return "c0ffee00-0000-0000-0000-000000000000"

    def add_message(self, conversation_id, role, content, **kwargs):
        self.messages.append((role, content))
        return len(self.messages)


def _server(store, accounts=("U1",), citations=()):
    from wealthryse_agent.agent_server import Services, create_app

    class Tools:
        closed = 0

        def call(self, name, arguments):
            return {"data": {"accounts": [{"account_id": a} for a in accounts]}}

        def close(self):
            Tools.closed += 1

    class FakeAgent:
        settings = SimpleNamespace(chat_reasoning_mode="pro")

        def run(self, **kwargs):
            store.runs.append(kwargs)
            yield {"type": "answer", "answer": "It is 1.2 [T1].", "citations": list(citations),
                   "turn": Turn(answer="It is 1.2 [T1].", citations=list(citations))}

    services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                        runtime=RuntimeSettings("", "", "", 1000, 90), tenant_id="t",
                        store_factory=lambda: store, agent_factory=FakeAgent, tools_factory=lambda u, c: Tools())
    services._store = store
    return TestClient(create_app(services))


def _events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class AgentServerGuideTest(unittest.TestCase):
    def test_explain_metric_runs_on_the_definitions_own_tools_and_returns_guide_links(self):
        store = _Store()
        cites = [{"id": "T1", "tool": "get_metric_definition", "sources": [{"source": "metrics_guide", "metric": "Beta"}]}]
        client = _server(store, citations=cites)
        response = client.post("/v1/explain-metric", json={"user_object_id": USER, "account_id": "U1", "metric": "Beta"})
        events = _events(response)
        self.assertEqual([e["type"] for e in events], ["start", "answer", "done"])
        self.assertEqual(events[1]["guide_links"], [{"name": "Beta", "link": "/metrics-guide#metric-beta"}])
        run = store.runs[0]
        self.assertTrue(run["skip_router"])
        self.assertEqual(run["allowed_tools"], ["get_metric_definition", "search_metrics_guide", "compare_to_benchmark",
                                                "get_risk_metrics", "get_nav_history", "get_performance", "calculate"])
        self.assertIn('"Beta"', run["instructions"])
        self.assertIn("/metrics-guide#metric-beta", run["instructions"])
        self.assertEqual(store.conversations[0][0], "U1")
        self.assertNotIn("reasoning_mode", run)  # pro is for website chat only

    def test_explain_metric_refuses_unknown_metrics_and_other_accounts(self):
        store = _Store()
        client = _server(store, accounts=("U2",))
        self.assertEqual(client.post("/v1/explain-metric", json={"user_object_id": USER, "account_id": "U1",
                                                                  "metric": "Made up"}).status_code, 404)
        self.assertEqual(client.post("/v1/explain-metric", json={"user_object_id": USER, "account_id": "U1",
                                                                  "metric": "Beta"}).status_code, 403)
        self.assertEqual(store.conversations, [])

    def test_guide_question_without_an_account_can_only_read_the_guide(self):
        store = _Store()
        client = _server(store)
        events = _events(client.post("/v1/guide-question", json={"user_object_id": USER, "question": "What is CVaR?"}))
        self.assertEqual(events[-1]["type"], "done")
        run = store.runs[0]
        self.assertEqual((run["account_id"], run["allowed_tools"]), (None, ["get_metric_definition", "search_metrics_guide"]))
        self.assertIn("No account was picked", run["instructions"])
        self.assertEqual(store.conversations[0][0], "guide:general")
        self.assertNotIn("reasoning_mode", run)

    def test_guide_question_with_an_account_checks_access_and_adds_its_tools(self):
        store = _Store()
        client = _server(store)
        _events(client.post("/v1/guide-question", json={"user_object_id": USER, "question": "Why did my risk go up?",
                                                        "account_id": "U1"}))
        run = store.runs[0]
        self.assertIn("get_risk_metrics", run["allowed_tools"])
        self.assertIn("calculate", run["allowed_tools"])
        self.assertNotIn("list_open_incidents", run["allowed_tools"])
        self.assertIn("account U1", run["instructions"])
        self.assertEqual(store.conversations[0][0], "guide:U1")
        denied = _server(_Store(), accounts=("U2",))
        self.assertEqual(denied.post("/v1/guide-question", json={"user_object_id": USER, "question": "x",
                                                                 "account_id": "U1"}).status_code, 403)

    def test_chat_can_use_the_guide_too(self):
        store = _Store()
        client = _server(store)
        _events(client.post("/v1/chat", json={"user_object_id": USER, "account_id": "U1", "message": "What is beta?"}))
        self.assertIsInstance(store.runs[0]["tools"], guide_tools.GuideTools)
        self.assertIn("search_metrics_guide", store.runs[0]["instructions"])
        self.assertEqual(store.runs[0]["reasoning_mode"], "pro")


def _website(mode="owner"):
    app = FastAPI()

    @app.get("/metrics-guide", response_class=HTMLResponse)
    def page() -> str:
        return "<html><body><main>guide</main></body></html>"

    with mock.patch.dict(os.environ, {"AGENT_UI_MODE": mode, "AGENT_API_URL": "https://agent.internal",
                                      "AGENT_API_AUDIENCE": "api://wealthryse-agent", "MONTH_TEAM_UI": "off"}):
        def signed_in(request: Request) -> dict:
            return OWNER

        agent_web.install(app, signed_in, lambda u: ["U1", "U2"], "", account_names=lambda u: {"U1": "Growth"})
    return TestClient(app)


class WebsiteGuideTest(unittest.TestCase):
    def test_guide_page_gets_the_script_only_when_the_agents_are_on(self):
        self.assertIn('<script src="/agents/guide.js" defer></script>', _website().get("/metrics-guide").text)
        self.assertNotIn("guide.js", _website("off").get("/metrics-guide").text)
        script = _website().get("/agents/guide.js").text
        for part in ("/api/agents/guide-question", "/api/agents/explain-metric", "Work it through with my numbers",
                     "Ask the guide", r"(\/metrics-guide#metric-[a-z0-9-]+)"):
            self.assertIn(part, script)

    def test_account_picker_lists_only_the_persons_accounts_with_names(self):
        self.assertEqual(_website().get("/api/agents/guide-accounts").json(),
                         {"accounts": [{"id": "U1", "name": "Growth"}, {"id": "U2", "name": "U2"}]})

    def test_proxies_check_the_account_and_metric_before_calling_the_agent(self):
        client = _website()
        with mock.patch.object(agent_web._AgentApi, "stream", return_value=iter([b"data: {}\n\n"])) as stream:
            self.assertEqual(client.post("/api/agents/explain-metric", json={"account_id": "U9", "metric": "Beta"}).status_code, 403)
            self.assertEqual(client.post("/api/agents/explain-metric", json={"account_id": "U1", "metric": "Nope"}).status_code, 404)
            self.assertEqual(client.post("/api/agents/guide-question", json={"question": "x", "account_id": "U9"}).status_code, 403)
            stream.assert_not_called()
            self.assertEqual(client.post("/api/agents/explain-metric", json={"account_id": "U1", "metric": "Beta"}).status_code, 200)
            self.assertEqual(client.post("/api/agents/guide-question", json={"question": "What is beta?"}).status_code, 200)
        self.assertEqual(stream.call_args_list[0].args, ("/v1/explain-metric", {
            "user_object_id": OWNER["object_id"], "account_id": "U1", "metric": "Beta", "conversation_id": None}))
        self.assertEqual(stream.call_args_list[1].args[1]["account_id"], None)

    def test_chat_answers_render_only_guide_links(self):
        script = agent_web.WIDGET_JS
        self.assertIn(r"(\/metrics-guide#metric-[a-z0-9-]+)", script)
        self.assertIn("guideLinks(e.guide_links)", script)


if __name__ == "__main__":
    unittest.main()
