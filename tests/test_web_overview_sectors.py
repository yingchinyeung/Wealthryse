"""Sector allocation endpoint, account directory figures and the batch 1 page markup."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from ibkr_etl import web
from ibkr_etl.agent_tools import AgentToolError

VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer"}
REQUEST = SimpleNamespace(state=SimpleNamespace(correlation_id="corr-1"))
ACCESS = {"subject_id": "U1", "reporting_currency": "USD", "history_start_date": "2025-01-01",
          "subject_type": "physical_account", "base_currency": "USD"}


@contextmanager
def _no_database():
    yield object()


class SectorEndpointTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(web, "_database", _no_database),
                        patch.object(web, "_portable_database_connection", lambda connection: connection)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_returns_the_validated_tool_data_for_an_authorized_account(self):
        payload = {"as_of": "2026-10-09", "quality_status": "pass",
                   "data": {"available": True, "allocation": [{"sector": "Technology", "weight": 1.0}],
                            "classification_coverage_pct": 100.0}}
        with patch.object(web, "_portfolio_access_context", return_value=ACCESS) as access, \
                patch.object(web, "execute_tool", return_value=payload) as tool:
            result = web.sector_allocation("U1", REQUEST, as_of="2026-10-09", user=VIEWER)
        access.assert_called_once_with("U1", VIEWER)
        _, context, name, arguments = tool.call_args.args
        self.assertEqual(name, "get_sector_allocation")
        self.assertEqual(arguments, {"subject_id": "U1", "as_of": "2026-10-09"})
        self.assertEqual(context.allowed_subject_ids, frozenset({"U1"}))
        self.assertEqual(result["allocation"][0]["sector"], "Technology")
        self.assertEqual(result["as_of"], "2026-10-09")

    def test_unauthorized_account_never_reaches_the_tool(self):
        with patch.object(web, "_portfolio_access_context",
                          side_effect=HTTPException(status_code=404, detail="Account not found")), \
                patch.object(web, "execute_tool") as tool, self.assertRaises(HTTPException) as raised:
            web.sector_allocation("U3", REQUEST, user=VIEWER)
        self.assertEqual(raised.exception.status_code, 404)
        tool.assert_not_called()

    def test_tool_refusal_becomes_an_unavailable_result(self):
        error = AgentToolError(409, "sector_reporting_fx_unavailable", "Needs FX.")
        with patch.object(web, "_portfolio_access_context", return_value=ACCESS), \
                patch.object(web, "execute_tool", side_effect=error):
            result = web.sector_allocation("U1", REQUEST, user=VIEWER)
        self.assertFalse(result["available"])
        self.assertEqual(result["allocation"], [])
        self.assertEqual(result["code"], "sector_reporting_fx_unavailable")

    def test_tool_permission_error_is_raised(self):
        error = AgentToolError(403, "subject_not_authorized", "No.")
        with patch.object(web, "_portfolio_access_context", return_value=ACCESS), \
                patch.object(web, "execute_tool", side_effect=error), \
                self.assertRaises(HTTPException) as raised:
            web.sector_allocation("U1", REQUEST, user=VIEWER)
        self.assertEqual(raised.exception.status_code, 403)


class AccountsOverviewTests(unittest.TestCase):
    def test_latest_nav_and_month_and_year_returns_per_account(self):
        directory = [{"subject_id": "U1", "reporting_currency": "USD"},
                     {"subject_id": "U2", "reporting_currency": "HKD"}]
        nav = [("2025-12-31", "100"), ("2026-09-30", "110"), ("2026-10-09", "121")]

        def access(subject_id, user):
            if subject_id == "U2":
                raise HTTPException(status_code=404, detail="hidden")
            return ACCESS

        with patch.object(web, "_account_directory", return_value=directory), \
                patch.object(web, "_portfolio_access_context", side_effect=access), \
                patch.object(web, "_database", _no_database), \
                patch.object(web, "_subject_nav_values", return_value=nav), \
                patch.object(web, "_subject_external_flows", return_value={}):
            result = web.accounts_overview(user=VIEWER)
        u1, u2 = result["accounts"]
        self.assertEqual(u1["nav"], 121.0)
        self.assertEqual(u1["nav_date"], "2026-10-09")
        self.assertAlmostEqual(u1["mtd"], 0.1)
        self.assertAlmostEqual(u1["ytd"], 0.21)
        self.assertEqual(u2, {"account_id": "U2", "currency": "HKD", "nav": None,
                              "nav_date": None, "mtd": None, "ytd": None})

    def test_flow_currency_mismatch_still_shows_nav(self):
        with patch.object(web, "_account_directory", return_value=[{"subject_id": "U1", "reporting_currency": "USD"}]), \
                patch.object(web, "_portfolio_access_context", return_value=ACCESS), \
                patch.object(web, "_database", _no_database), \
                patch.object(web, "_subject_nav_values", return_value=[("2026-10-09", "50")]), \
                patch.object(web, "_subject_external_flows", side_effect=HTTPException(status_code=409, detail="x")):
            account = web.accounts_overview(user=VIEWER)["accounts"][0]
        self.assertEqual(account["nav"], 50.0)
        self.assertIsNone(account["mtd"])


class PageMarkupTests(unittest.TestCase):
    OWNER = {"email": "owner@example.com", "role": "owner"}

    def test_home_cards_load_figures(self):
        page = web.home(None, self.OWNER)
        self.assertIn('data-account-stats="U16088571"', page)
        self.assertIn("/api/accounts/overview", page)
        self.assertIn('id="accounts-summary"', page)

    def test_overview_shows_returns_holdings_and_sectors(self):
        page = web._render_account_page(self.OWNER, account_id="U16088571", section="overview")
        for marker in ('id="overview-returns"', 'id="overview-holdings"', 'id="overview-sectors"',
                       "renderOverview(asOf,s)", 'id="positions"'):
            self.assertIn(marker, page)
        self.assertNotIn('id="obs"', page)
        self.assertNotIn("#obs", page)

    def test_holdings_tab_has_sector_card_filter_and_sortable_table(self):
        page = web._render_account_page(self.OWNER, account_id="U16088571", section="holdings")
        for marker in ('id="sector-bars"', 'id="holdings-filter"', "holdingsRows(h)",
                       "wrTable('#holdings'", "window.wrTable=", "Industries within each sector"):
            self.assertIn(marker, page)

    def test_combined_holdings_is_sortable_and_filterable(self):
        page = web._render_platform_page(self.OWNER, section="combined-holdings")
        self.assertIn('id="combined-filter"', page)
        self.assertIn("wrTable('#combined-holdings'", page)

    def test_web_source_carries_the_overview_changes(self):
        live = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        for marker in ('@app.get("/api/portfolio/{account_id}/sectors")', '@app.get("/api/accounts/overview")',
                       "from .web_ui import ACCOUNT_UI_SCRIPT, HOME_SCRIPT, TABLE_SCRIPT",
                       'id="overview-returns"', 'id="sector-bars"', "holdingsRows(h)",
                       "{TABLE_SCRIPT}<script>", "{ACCOUNT_UI_SCRIPT}", "{HOME_SCRIPT}</body>",
                       "wrTable('#combined-holdings'"):
            self.assertIn(marker, live)
        self.assertNotIn("#obs", live)



class NavigationAndChartTests(unittest.TestCase):
    def test_operations_menu_groups_operator_pages_by_role(self):
        owner = web._platform_navigation({"role": "owner"}, active="administration")
        self.assertIn('<summary class="platform-nav-link active">Operations</summary>', owner)
        for href in ("/system-health", "/ibkr-connections", "/account-onboarding", "/administration"):
            self.assertIn(f'class="platform-menu-link', owner)
            self.assertIn(f'href="{href}"', owner)
        self.assertIn('aria-current="page">Administration</a>', owner)
        viewer = web._platform_navigation({"role": "viewer"})
        self.assertIn('href="/system-health"', viewer)
        self.assertNotIn('href="/administration"', viewer)
        self.assertNotIn('href="/account-onboarding"', viewer)
        self.assertNotIn("platform-nav-link active", viewer)

    def test_account_switcher_lists_visible_accounts_and_keeps_the_tab(self):
        directory = [{"subject_id": "U16088571", "display_name": "Tech"},
                     {"subject_id": "alpha-world-t", "display_name": "Alpha <T>"}]
        with patch.object(web, "_account_directory", return_value=directory):
            page = web._render_account_page({"email": "o@example.com", "role": "owner"},
                                            account_id="U16088571", section="holdings")
        self.assertIn('id="account-switcher"', page)
        self.assertIn("+'/holdings'", page)
        self.assertIn('<option value="U16088571" selected>Tech</option>', page)
        self.assertIn("Alpha &lt;T&gt;", page)
        with patch.object(web, "_account_directory", side_effect=RuntimeError("db down")):
            page = web._render_account_page({"email": "o@example.com", "role": "owner"},
                                            account_id="U16088571", section="holdings")
        self.assertNotIn('id="account-switcher"', page)

    def test_charts_are_sharp_and_have_period_buttons(self):
        for source in (web._render_account_page({"email": "o@example.com", "role": "owner"},
                                                account_id="U16088571", section="performance"),
                       Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")):
            self.assertIn("ctx.setTransform(dpr,0,0,dpr,0,0)", source)
            self.assertIn('data-chart="#chart"', source)
            self.assertIn('data-chart="#benchmark-chart"', source)
            self.assertIn("series=rangeSeries(id,series,options)", source)
            self.assertIn("showFreshness(d.latest)", source)
            self.assertIn('id="freshness"', source)
            self.assertNotIn("c.width/r.width", source)


class PhoneLayoutTests(unittest.TestCase):
    def test_data_tables_stack_on_phones_with_column_labels(self):
        from ibkr_etl.web_theme import ACCOUNT_THEME_CSS
        from ibkr_etl.web_ui import TABLE_SCRIPT
        self.assertIn("table.stack-mobile thead { display: none; }", ACCOUNT_THEME_CSS)
        self.assertIn("content: attr(data-label)", ACCOUNT_THEME_CSS)
        self.assertIn("cell.dataset.label=names[i]", TABLE_SCRIPT)
        self.assertIn("position: sticky; top: 76px", ACCOUNT_THEME_CSS)


class PerformanceRiskLayoutTests(unittest.TestCase):
    OWNER = {"email": "o@example.com", "role": "owner"}

    @staticmethod
    def view(page, section):
        start = page.index(f'data-section="{section}"')
        return page[start:page.index('<div class="page-view', start)]

    def test_performance_leads_with_the_chart_and_risk_holds_the_risk_cards(self):
        page = web._render_account_page(self.OWNER, account_id="U16088571", section="performance")
        performance, risk = self.view(page, "performance"), self.view(page, "risk")
        order = [performance.index(marker) for marker in ('id="perf-chart"', 'id="perf-returns"', 'id="perf-calendar"',
                                                          'id="perf-attribution"', 'id="perf-benchmark"', 'id="perf-flows"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('data-mode="benchmark"', performance)
        self.assertIn('<canvas id="benchmark-chart"', performance)
        for marker in ('id="analytics-statistics"', 'id="drawdown-chart"', 'id="rolling-volatility-chart"',
                       'id="rolling-beta-chart"', 'id="correlation-body"'):
            self.assertIn(marker, risk)
            self.assertNotIn(marker, performance)
        self.assertIn("activeSection==='performance'||activeSection==='risk')void renderAnalytics(asOf)", page)
        self.assertIn("if(activeSection==='risk'){if(selected)fetch(", page)

    def test_section_jumps_and_no_repeated_header(self):
        performance = web._render_account_page(self.OWNER, account_id="U16088571", section="performance")
        self.assertIn('<a href="#perf-chart">NAV chart</a>', performance)
        self.assertIn('<h2 class="sr-only">Performance</h2>', performance)
        self.assertNotIn("Account workspace", performance)
        self.assertLess(performance.index('id="asof-select"'), performance.index('class="account-sticky"'))
        risk = web._render_account_page(self.OWNER, account_id="U16088571", section="risk")
        self.assertIn('<a href="#risk-drawdowns">Drawdowns</a>', risk)
        cash = web._render_account_page(self.OWNER, account_id="U16088571", section="cash")
        self.assertNotIn('class="section-jumps"', cash)
        guide = web._render_platform_page(self.OWNER, section="metrics-guide")
        self.assertNotIn("Platform workspace", guide)

    def test_web_source_has_the_layout(self):
        live = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        for marker in ('class="account-hero-controls"', "{section_jumps}</div>", 'id="perf-chart"',
                       "{RISK_ANALYTICS_CARDS}{CORRELATION_CARD}</div>", "esc(asOfLabel(x))",
                       "activeSection==='performance'||activeSection==='risk'"):
            self.assertIn(marker, live)
        self.assertNotIn("Account workspace", live)
        self.assertNotIn("Platform workspace", live)


class FailureStateTests(unittest.TestCase):
    OWNER = {"email": "o@example.com", "role": "owner"}

    def test_failures_stay_in_their_card(self):
        for source in (web._render_account_page(self.OWNER, account_id="U16088571", section="risk"),
                       Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")):
            self.assertNotIn("Unable to load validated portfolio data", source)
            self.assertIn("loadFailure(error,", source)
            self.assertIn("/risk${suffix}`).then(json).catch(", source.replace("{{", "{").replace("}}", "}"))
            self.assertIn("benchmark-status`).then(json).catch(", source)
            self.assertIn("emptyMessage:p.failed?", source)
            self.assertIn("state.options.emptyMessage", source)
        page = web._render_account_page(self.OWNER, account_id="U16088571", section="risk")
        self.assertIn("const pageError=message=>", page)
        self.assertIn("emptyMessage:'Drawdowns need at least two NAV dates.'", page)


class DarkModeTests(unittest.TestCase):
    def test_dark_theme_follows_the_computer_setting_on_every_page(self):
        from ibkr_etl.web_theme import ACCOUNT_THEME_CSS, DARK_THEME_CSS, HOME_THEME_CSS
        self.assertIn("@media screen and (prefers-color-scheme: dark)", DARK_THEME_CSS)
        self.assertTrue(ACCOUNT_THEME_CSS.endswith(DARK_THEME_CSS))
        self.assertTrue(HOME_THEME_CSS.endswith(DARK_THEME_CSS))
        for token in ("--bg:", "--surface:", "--ink:", "--chart-grid:", "--chart-text:"):
            self.assertIn(token, DARK_THEME_CSS)

    def test_chart_lines_and_labels_use_theme_colours(self):
        for source in (web._render_account_page({"email": "o@example.com", "role": "owner"},
                                                account_id="U16088571", section="performance"),
                       Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")):
            self.assertIn("chartInk('--chart-grid','#e7ebf2')", source)
            self.assertNotIn("ctx.strokeStyle='#e7ebf2'", source)
            self.assertNotIn("ctx.fillStyle='#667085'", source)


if __name__ == "__main__":
    unittest.main()
