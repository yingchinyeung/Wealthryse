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

    def _ai_analyst_tab(self, mode: str) -> str:
        with mock.patch.dict(os.environ, {"AGENT_UI_MODE": mode}), \
                mock.patch.object(web, "_account_directory", return_value=[]), \
                mock.patch.object(web, "AI_ANALYST_CONFIG", replace(web.AI_ANALYST_CONFIG, enabled=False)):
            return web._render_account_page(OWNER, account_id="U1", section="ai-analyst", access=dict(ACCESS))

    def test_ai_analyst_tab_hands_over_to_the_agents_analyst_while_the_older_model_is_off(self):
        page = self._ai_analyst_tab("owner")
        self.assertIn("data-agent-home", page)
        self.assertIn('data-agent-open data-question="What if Hong Kong falls 10%?"', page)
        self.assertIn('<div class="card wide ai-chat-card" style="display:none">', page)
        self.assertIn('id="analysis-run"', page)  # still in the page for its script

    def test_ai_analyst_tab_is_unchanged_when_the_agents_are_off(self):
        page = self._ai_analyst_tab("off")
        self.assertNotIn("data-agent-home", page)
        self.assertIn('<div class="card wide ai-chat-card">', page)


if __name__ == "__main__":
    unittest.main()
