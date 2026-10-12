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


def _app(mode: str, user: dict) -> TestClient:
    app = FastAPI()

    @app.get("/accounts/{account}/{section}", response_class=HTMLResponse)
    def page(account: str, section: str) -> str:
        return "<html><body><main>page</main></body></html>"

    with mock.patch.dict(os.environ, {"AGENT_UI_MODE": mode, "AGENT_API_URL": "https://agent.internal",
                                      "AGENT_API_AUDIENCE": "api://wealthryse-agent"}):
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

    def test_widget_is_added_only_to_the_three_pages(self):
        client = _app("owner", OWNER)
        self.assertIn('<script src="/agents/widget.js" defer></script></body>', client.get("/accounts/U1/holdings").text)
        self.assertNotIn("widget.js", client.get("/accounts/U1/cash").text)
        self.assertEqual(client.get("/agents/widget.js").headers["content-type"].split(";")[0], "application/javascript")

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


if __name__ == "__main__":
    unittest.main()
