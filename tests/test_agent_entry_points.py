"""Where the website offers the agents: the navigation, every account tab and the AI analyst tab."""
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest import mock

from ibkr_etl import web

OWNER = {"object_id": "11111111-1111-1111-1111-111111111111", "email": "o@example.invalid", "role": "owner"}
VIEWER = {"object_id": "22222222-2222-2222-2222-222222222222", "email": "v@example.invalid", "role": "viewer"}
ACCESS = {"subject_id": "U1", "display_name": "Growth", "reporting_currency": "USD", "history_start_date": "2025-01-20",
          "lifecycle_status": "active", "preview_mode": False, "feature_states": {}}


class AgentEntryPointTests(unittest.TestCase):
    def test_navigation_links_the_agent_inbox_only_when_the_agents_are_on_for_that_person(self):
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "owner", "MONTH_TEAM_UI": "owner"}):
            owner = web._platform_navigation(OWNER, active="agent-inbox")
            viewer = web._platform_navigation(VIEWER)
        self.assertIn('href="/agent-inbox" data-agent-inbox aria-current="page">Agent inbox</a>', owner)
        self.assertIn('href="/month-end"', owner)
        self.assertNotIn("/agent-inbox", viewer)
        self.assertNotIn("/month-end", viewer)
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "off", "MONTH_TEAM_UI": "off"}):
            self.assertNotIn("/agent-inbox", web._platform_navigation(OWNER))

    def _page(self, mode: str, section: str = "overview") -> str:
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": mode}), \
                mock.patch.object(web, "_account_directory", return_value=[]), \
                mock.patch.object(web, "AI_ANALYST_CONFIG", replace(web.AI_ANALYST_CONFIG, enabled=False)):
            return web._render_account_page(OWNER, account_id="U1", section=section, access=dict(ACCESS))

    def test_ai_analyst_tab_is_left_out_while_the_agents_analyst_replaces_it(self):
        self.assertNotIn('href="/accounts/U1/ai-analyst"', self._page("owner"))
        self.assertIn('href="/accounts/U1/ai-analyst"', self._page("off"))
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "owner"}), \
                mock.patch.object(web, "_portfolio_access_context", return_value=dict(ACCESS)), \
                mock.patch.object(web, "AI_ANALYST_CONFIG", replace(web.AI_ANALYST_CONFIG, enabled=False)):
            response = web.account_page("U1", "ai-analyst", None, OWNER)
        self.assertEqual((response.status_code, response.headers["location"]), (307, "/accounts/U1/overview#ask"))

    def test_ai_analyst_tab_stays_while_the_older_analysts_model_is_on(self):
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": "owner"}), \
                mock.patch.object(web, "AI_ANALYST_CONFIG", replace(web.AI_ANALYST_CONFIG, enabled=True)):
            self.assertFalse(web._agent_analyst_replaces_tab(OWNER))


if __name__ == "__main__":
    unittest.main()
