from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from ibkr_etl import agent_web

OWNER = {"object_id": "11111111-1111-1111-1111-111111111111", "email": "o@example.invalid", "role": "owner"}
VIEWER = {"object_id": "22222222-2222-2222-2222-222222222222", "email": "v@example.invalid", "role": "viewer"}


def _app(mode: str, user: dict, month: str = "off") -> TestClient:
    app = FastAPI()

    @app.get("/accounts/{account}/{section}", response_class=HTMLResponse)
    def page(account: str, section: str) -> str:
        return "<html><body><main>page</main></body></html>"

    with mock.patch.dict(os.environ, {"AGENT_UI_MODE": mode, "AGENT_API_URL": "https://agent.internal",
                                      "AGENT_API_AUDIENCE": "api://wealthryse-agent", "MONTH_TEAM_UI": month}):
        def signed_in(request: Request) -> dict:
            return user

        agent_web.install(app, signed_in, lambda u: ["U1"], "")
    return TestClient(app)


class AgentWebTests(unittest.TestCase):
    def test_off_by_default(self):
        client = _app("off", OWNER)
        self.assertEqual(client.get("/api/agents/status").json(), {"enabled": False})
        self.assertNotIn("widget.js", client.get("/accounts/U1/overview").text)
        self.assertEqual(client.post("/api/agents/chat", json={"account_id": "U1", "message": "hi"}).status_code, 404)

    def test_owner_mode_is_owner_only(self):
        self.assertTrue(_app("owner", OWNER).get("/api/agents/status").json()["enabled"])
        viewer = _app("owner", VIEWER)
        self.assertFalse(viewer.get("/api/agents/status").json()["enabled"])
        self.assertEqual(viewer.get("/api/agents/inbox").status_code, 404)

    def test_widget_is_on_every_page_and_the_chat_on_every_account_tab(self):
        client = _app("owner", OWNER)
        for path in ("/accounts/U1/holdings", "/accounts/U1/cash", "/accounts/U1/risk", "/accounts/U1/ai-analyst"):
            self.assertIn('<script src="/agents/widget.js" defer></script></body>', client.get(path).text)
        self.assertEqual(client.get("/agents/widget.js").headers["content-type"].split(";")[0], "application/javascript")
        script = client.get("/agents/widget.js").text
        self.assertIn(r"/^\/accounts\/([^/]+)\/([^/]+)$/", script)
        self.assertIn("a[data-agent-inbox]", script)  # the navigation badge
        self.assertIn("What if Hong Kong falls 10%?", script)  # a starter question on the Risk tab
        self.assertIn("e.key==='Escape'", script)

    def test_ui_enabled_for_matches_the_mode(self):
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "owner"}):
            self.assertTrue(agent_web.ui_enabled_for(OWNER))
            self.assertFalse(agent_web.ui_enabled_for(VIEWER))
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "all"}):
            self.assertTrue(agent_web.ui_enabled_for(VIEWER))
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "off"}):
            self.assertFalse(agent_web.ui_enabled_for(OWNER))

    def test_inbox_page_uses_the_website_shell_and_account_names(self):
        app = FastAPI()
        shell_calls = []

        def shell(user, *, active, title, description, content, script):
            shell_calls.append(active)
            return f"<html><body><nav>site</nav><h1>{title}</h1>{content}{script}</body></html>"

        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "owner", "AGENT_API_URL": "https://agent.internal",
                                          "AGENT_API_AUDIENCE": "api://wealthryse-agent", "MONTH_TEAM_UI": "owner"}):
            agent_web.install(app, lambda: OWNER, lambda u: ["U1"], "", render_page=shell,
                              account_names=lambda u: {"U1": "U1 - Growth </script>"})
            client = TestClient(app)
            page = client.get("/agent-inbox").text
            month = client.get("/month-end").text
        self.assertIn("<nav>site</nav>", page)
        self.assertIn('const NAMES={"U1": "U1 - Growth <\\/script>"};', page)
        self.assertIn("weekly_brief:'Weekly brief'", page)
        self.assertIn("live_alert:'Live alert'", page)
        self.assertIn('href="/month-end"', page)
        self.assertEqual(shell_calls, ["agent-inbox", "month-end"])
        self.assertIn('id="rows"', month)
        self.assertNotIn("<title>Month-end</title>", month)

    def test_chat_checks_the_website_account_list_first(self):
        client = _app("all", VIEWER)
        with mock.patch.object(agent_web._AgentApi, "stream") as stream:
            response = client.post("/api/agents/chat", json={"account_id": "U2", "message": "hi"})
        self.assertEqual(response.status_code, 403)
        stream.assert_not_called()

    def test_chat_names_the_signed_in_person(self):
        client = _app("all", VIEWER)
        with mock.patch.object(agent_web._AgentApi, "stream", return_value=iter([b"data: {}\n\n"])) as stream:
            response = client.post("/api/agents/chat", json={"account_id": "U1", "message": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stream.call_args.args[1]["user_object_id"], VIEWER["object_id"])

    def test_extra_fields_are_rejected(self):
        client = _app("all", VIEWER)
        response = client.post("/api/agents/chat", json={"account_id": "U1", "message": "hi", "user_object_id": "x"})
        self.assertEqual(response.status_code, 422)

    def test_memory_is_always_the_signed_in_persons_own(self):
        client = _app("all", VIEWER)
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"memories": []}) as request:
            self.assertEqual(client.get("/api/agents/memory").status_code, 200)
            self.assertEqual(client.put("/api/agents/memory/tone", json={"value": "plain"}).status_code, 200)
            self.assertEqual(client.delete("/api/agents/memory/tone").status_code, 200)
            self.assertEqual(client.put("/api/agents/memory/tone",
                                        json={"value": "plain", "user_object_id": OWNER["object_id"]}).status_code, 422)
        calls = request.call_args_list
        self.assertEqual(calls[0].kwargs["params"]["user_object_id"], VIEWER["object_id"])
        self.assertEqual((calls[1].args, calls[1].kwargs["json"]),
                         (("PUT", "/v1/memory/tone"), {"user_object_id": VIEWER["object_id"], "value": "plain"}))
        self.assertEqual(calls[2].args, ("DELETE", "/v1/memory/tone"))
        self.assertEqual(len(calls), 3)
        self.assertEqual(_app("owner", VIEWER).get("/api/agents/memory").status_code, 404)


class MonthTeamWebTests(unittest.TestCase):
    CARDS = [{"item_id": 1, "kind": "holdings_explainer"}, {"item_id": 2, "kind": "month_end_hold"},
             {"item_id": 3, "kind": "month_end_report"}]

    def month_on(self) -> None:
        patcher = mock.patch.dict(os.environ, {"MONTH_TEAM_UI": "owner"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_off_by_default(self):
        client = _app("owner", OWNER)
        self.assertNotIn("month-team.js", client.get("/accounts/U1/reports").text)
        self.assertFalse(client.get("/api/agents/month-end/status").json()["enabled"])
        self.assertEqual(client.get("/month-end").status_code, 404)
        self.assertEqual(client.get("/api/agents/month-end/runs").status_code, 404)
        self.assertEqual(client.get("/api/agents/month-end/rounds").status_code, 404)

    def test_owner_gets_the_panel_on_the_reports_tab_and_the_month_end_page(self):
        self.month_on()
        client = _app("owner", OWNER, month="owner")
        self.assertIn('<script src="/agents/month-team.js" defer></script></body>', client.get("/accounts/U1/reports").text)
        holdings = client.get("/accounts/U1/holdings").text
        self.assertIn("widget.js", holdings)
        self.assertNotIn("month-team.js", holdings)
        self.assertEqual(client.get("/agents/month-team.js").headers["content-type"].split(";")[0], "application/javascript")
        self.assertIn("<title>Month-end</title>", client.get("/month-end").text)
        self.assertIn("data-why=", client.get("/month-end").text)
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"objections": []}) as request:
            client.get("/api/agents/month-end/runs/U1/2026-09-30", params={"mode": "replay"})
        self.assertEqual(request.call_args.kwargs["params"]["mode"], "replay")
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"runs": []}) as request:
            client.get("/api/agents/month-end/runs", params={"period_end": "2026-09-30"})
            client.get("/api/agents/month-end/runs/U1/2026-09-30")
        self.assertEqual(request.call_args_list[0].kwargs["params"],
                         {"user_object_id": OWNER["object_id"], "mode": "month", "period_end": "2026-09-30"})
        self.assertEqual(request.call_args_list[1].args[1], "/v1/month-end/runs/U1/2026-09-30")
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"rounds": []}) as request:
            client.get("/api/agents/month-end/runs", params={"mode": "replay", "round": 2})
            client.get("/api/agents/month-end/runs/U1/2026-09-30", params={"mode": "replay", "round": 2})
            client.get("/api/agents/month-end/rounds")
            self.assertEqual(client.get("/api/agents/month-end/runs", params={"round": 0}).status_code, 422)
        self.assertEqual(request.call_args_list[0].kwargs["params"]["round"], 2)
        self.assertEqual(request.call_args_list[1].kwargs["params"]["round"], 2)
        self.assertEqual(request.call_args_list[2].args[1], "/v1/month-end/rounds")
        self.assertEqual(request.call_count, 3)
        self.assertIn('id="rounds"', client.get("/month-end").text)

    def test_owner_cannot_read_another_accounts_run(self):
        self.month_on()
        client = _app("owner", OWNER, month="owner")
        with mock.patch.object(agent_web._AgentApi, "request") as request:
            self.assertEqual(client.get("/api/agents/month-end/runs/U2/2026-09-30").status_code, 403)
            self.assertEqual(client.get("/api/agents/month-end/runs/U1/30-09-2026").status_code, 400)
            self.assertEqual(client.get("/api/agents/month-end/runs", params={"mode": "live"}).status_code, 422)
        request.assert_not_called()

    def test_only_the_owner_sees_or_decides_month_end_cards(self):
        self.month_on()
        viewer = _app("all", VIEWER, month="owner")
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"items": self.CARDS}) as request:
            self.assertEqual([x["item_id"] for x in viewer.get("/api/agents/inbox").json()["items"]], [1])
            self.assertEqual(viewer.post("/api/agents/inbox/2/decision", json={"decision": "approved"}).status_code, 404)
            self.assertEqual(viewer.get("/month-end").status_code, 404)
            self.assertNotIn("/month-end", viewer.get("/agent-inbox").text)
            self.assertEqual(viewer.post("/api/agents/inbox/1/decision", json={"decision": "read"}).status_code, 200)
        self.assertEqual(request.call_args.args[:2], ("POST", "/v1/inbox/1/decision"))
        owner = _app("owner", OWNER, month="owner")
        with mock.patch.object(agent_web._AgentApi, "request", return_value={"items": self.CARDS}):
            self.assertEqual(len(owner.get("/api/agents/inbox").json()["items"]), 3)
            self.assertIn('href="/month-end"', owner.get("/agent-inbox").text)


if __name__ == "__main__":
    unittest.main()
