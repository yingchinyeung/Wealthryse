"""WealthRyse Next: the WEB_DESIGN switch, the per-browser choice and the page transform."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from ibkr_etl import web, web_next
from ibkr_etl.web_ui import HOME_SCRIPT

OWNER = {"x-ms-client-principal-id": "owner-1", "x-ms-client-principal-name": "owner@example.com"}
VIEWER = {"x-ms-client-principal-id": "viewer-1", "x-ms-client-principal-name": "viewer@example.com"}
PAGE = '<html lang="en"><head><style>x</style></head><body><header><div class="session">a · Owner</div></header>{s}</body></html>'


def setting(value):
    return mock.patch.dict(os.environ, {"WEB_DESIGN": value})


class SwitchTest(unittest.TestCase):
    def test_classic_is_the_default_and_unknown_values_fall_back_to_it(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(web_next.setting(), "classic")
        with setting("sparkly"):
            self.assertEqual(web_next.setting(), "classic")
            self.assertEqual(web_next.design_for("next", "owner"), "classic")

    def test_preview_is_owner_only_and_opt_in(self):
        with setting("preview"):
            self.assertEqual(web_next.design_for(None, "owner"), "classic")
            self.assertEqual(web_next.design_for("next", "owner"), "next")
            self.assertEqual(web_next.design_for("next", "viewer"), "classic")
            self.assertTrue(web_next.may_choose("next", "owner"))
            self.assertFalse(web_next.may_choose("next", "viewer"))
            self.assertTrue(web_next.may_choose("classic", "viewer"))

    def test_next_is_for_everyone_and_classic_is_retired(self):
        with setting("next"):
            self.assertEqual(web_next.design_for(None, "viewer"), "next")
            self.assertEqual(web_next.design_for("classic", "owner"), "next")
            self.assertTrue(web_next.may_choose("next", "viewer"))
            self.assertFalse(web_next.may_choose("classic", "owner"))

    def test_nothing_can_be_chosen_while_the_switch_is_classic(self):
        with setting("classic"):
            self.assertFalse(web_next.may_choose("next", "owner"))
            self.assertFalse(web_next.may_choose("classic", "owner"))
            self.assertFalse(web_next.may_choose("other", "owner"))

    def test_return_path_stays_on_this_site(self):
        for bad in ("https://evil.example", "//evil.example", "/\\evil", "", None):
            self.assertEqual(web_next.safe_return_path(bad), "/")
        self.assertEqual(web_next.safe_return_path("/accounts/U1/risk?x=1"), "/accounts/U1/risk?x=1")


class TransformTest(unittest.TestCase):
    def test_classic_pages_are_unchanged(self):
        page = PAGE.format(s=HOME_SCRIPT)
        with setting("next"):
            self.assertEqual(web_next.transform(page, design="classic", role="viewer", back="/",
                                                page_scripts={}, metric_anchors={}), page)

    def test_preview_offers_owners_the_new_design(self):
        with setting("preview"):
            out = web_next.transform(PAGE.format(s=""), design="classic", role="owner", back="/accounts/U1/risk",
                                     page_scripts={}, metric_anchors={})
        self.assertIn('href="/design/next?back=%2Faccounts%2FU1%2Frisk"', out)

    def test_next_adds_the_design_and_keeps_the_classic_theme(self):
        page = PAGE.format(s=HOME_SCRIPT) + "@media screen {"
        with setting("next"):
            out = web_next.transform(page, design="next", role="owner", back="/",
                                     page_scripts={"home": HOME_SCRIPT}, metric_anchors={"Beta": "metric-beta</script>"})
        self.assertIn('<html lang="en" data-design="next">', out)
        self.assertIn('<style id="nx-css">', out)
        self.assertIn('type="speculationrules"', out)
        self.assertIn("window.WR_NEXT=", out)
        self.assertIn("data-nx-pin", out)  # the rail collapses to icons and can be pinned open
        self.assertNotIn(HOME_SCRIPT, out)
        self.assertIn("@media screen {", out)  # the classic light and dark themes stay
        self.assertNotIn("metric-beta</script>", out)  # config can't close the script early
        self.assertLess(out.index("nx-css"), out.index("</head>"))


    def test_page_redesign_is_off_by_default(self):
        with mock.patch.dict(os.environ, {"WEB_DESIGN": "next"}, clear=True):
            self.assertEqual(web_next.pages_setting(), "off")
            self.assertFalse(web_next.pages_for("owner"))
            out = web_next.transform(PAGE.format(s=""), design="next", role="owner", back="/",
                                     page_scripts={}, metric_anchors={})
        self.assertNotIn("nx-pages-css", out)
        self.assertNotIn("wr-next-last", out)

    def test_page_redesign_preview_is_owner_only(self):
        with mock.patch.dict(os.environ, {"WEB_DESIGN": "next", "WEB_NEXT_PAGES": "preview"}):
            self.assertTrue(web_next.pages_for("owner"))
            self.assertFalse(web_next.pages_for("viewer"))
            owner = web_next.transform(PAGE.format(s=""), design="next", role="owner", back="/",
                                       page_scripts={}, metric_anchors={})
            viewer = web_next.transform(PAGE.format(s=""), design="next", role="viewer", back="/",
                                        page_scripts={}, metric_anchors={})
        self.assertIn('<style id="nx-pages-css">', owner)
        self.assertIn("wr-next-last", owner)
        self.assertLess(owner.index("nx-pages-css"), owner.index("</head>"))
        self.assertNotIn("nx-pages-css", viewer)

    def test_page_redesign_on_is_for_everyone_but_needs_the_new_design(self):
        with mock.patch.dict(os.environ, {"WEB_DESIGN": "classic", "WEB_NEXT_PAGES": "on"}):
            self.assertTrue(web_next.pages_for("viewer"))
            classic = web_next.transform(PAGE.format(s=""), design="classic", role="viewer", back="/",
                                         page_scripts={}, metric_anchors={})
        self.assertNotIn("nx-pages-css", classic)
        with mock.patch.dict(os.environ, {"WEB_NEXT_PAGES": "sparkly"}):
            self.assertEqual(web_next.pages_setting(), "off")


class AppTest(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(web, "AUTHORIZATION_MODE", "legacy"),
            mock.patch.object(web, "ALLOWED_EMAILS", {"owner@example.com"}),
            mock.patch.object(web, "ALLOWED_OBJECT_IDS", set()),
            mock.patch.object(web, "VIEWER_EMAILS", {"viewer@example.com"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(web.app)

    def test_classic_switch_serves_the_classic_page(self):
        with setting("classic"):
            page = self.client.get("/metrics-guide", headers=OWNER, cookies={"wr_design": "next"}).text
            self.assertNotIn('data-design="next"', page)
            self.assertNotIn("Try the new design", page)
            self.assertEqual(self.client.get("/design/next", headers=OWNER, follow_redirects=False).status_code, 404)

    def test_owner_turns_the_new_design_on_for_their_browser(self):
        with setting("preview"):
            self.assertIn("Try the new design", self.client.get("/metrics-guide", headers=OWNER).text)
            response = self.client.get("/design/next?back=/metrics-guide", headers=OWNER, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/metrics-guide")
            cookie = response.headers["set-cookie"]
            self.assertIn("wr_design=next", cookie)
            self.assertIn("HttpOnly", cookie)
            page = self.client.get("/metrics-guide", headers=OWNER, cookies={"wr_design": "next"}).text
            self.assertIn('data-design="next"', page)
            self.assertIn("Content-Security-Policy",
                          self.client.get("/metrics-guide", headers=OWNER, cookies={"wr_design": "next"}).headers)

    def test_viewers_keep_classic_during_the_preview(self):
        with setting("preview"):
            self.assertEqual(self.client.get("/design/next", headers=VIEWER, follow_redirects=False).status_code, 404)
            page = self.client.get("/metrics-guide", headers=VIEWER, cookies={"wr_design": "next"}).text
            self.assertNotIn('data-design="next"', page)
            self.assertNotIn("Try the new design", page)

    def test_everyone_gets_next_with_no_way_back(self):
        with setting("next"):
            self.assertIn('data-design="next"', self.client.get("/metrics-guide", headers=VIEWER).text)
            self.assertEqual(self.client.get("/design/classic", headers=OWNER, follow_redirects=False).status_code, 404)
            page = self.client.get("/metrics-guide", headers=OWNER, cookies={"wr_design": "classic"}).text
            self.assertIn('data-design="next"', page)  # an old classic choice is ignored
            self.assertIn('"canClassic": false', page)
            response = self.client.get("/design/next?back=https://evil.example", headers=VIEWER,
                                       follow_redirects=False)
            self.assertEqual(response.headers["location"], "/")

    def test_owner_can_go_back_during_the_preview(self):
        with setting("preview"):
            self.assertEqual(self.client.get("/design/classic", headers=OWNER, follow_redirects=False).status_code, 303)

    def test_api_answers_are_never_touched(self):
        with setting("next"), mock.patch.object(web, "_account_directory", return_value=[]):
            response = self.client.get("/api/accounts/overview", headers=OWNER)
        self.assertEqual(response.json(), {"accounts": []})

    def test_home_page_hands_its_cards_to_the_new_design(self):
        with setting("next"):
            page = self.client.get("/", headers=OWNER).text
        self.assertIn('data-design="next"', page)
        self.assertNotIn(HOME_SCRIPT, page)
        self.assertIn('class="account-card"', page)  # the briefing reads the cards


if __name__ == "__main__":
    unittest.main()
