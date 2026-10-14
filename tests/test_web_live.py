"""The website's live IBKR view: who sees it, how fresh it is, and the page panels."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

from ibkr_etl import live_ui, live_view, web

NOW = datetime(2026, 10, 13, 14, 35, tzinfo=timezone.utc)  # US market open
WEEKEND = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
OWNER = {"email": "owner@example.com", "object_id": "owner-1", "role": "owner", "roles": ["owner"],
         "authorization_source": "database"}
VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer", "roles": ["viewer"],
          "authorization_source": "database"}
LEGACY_OWNER = {"email": "owner@example.com", "object_id": "owner-1", "role": "owner"}


def _access(state=None, **overrides):
    access = {"subject_id": "U1", "display_name": "Test account", "reporting_currency": "USD",
              "history_start_date": "2025-01-01", "subject_type": "physical_account",
              "base_currency": "USD", "lifecycle_status": "active", "preview_mode": False,
              "feature_states": {"website_analytics": "active"}}
    if state:
        access["feature_states"] = dict(access["feature_states"], live_view=state)
    access.update(overrides)
    return access


class FakeConnection:
    """Answers the live view's queries with canned rows, recording what was asked."""

    def __init__(self, status=None, summary=None, delayed=0, positions=(), trades=()):
        self.answers = {
            "FROM live_gateway_status": [status] if status else [],
            "FROM subject_live_summary": [summary] if summary else [],
            "SELECT count(*) AS delayed": [{"delayed": delayed}],
            "FROM subject_live_positions": list(positions),
            "FROM live_executions": list(trades),
        }
        self.queries = []

    def execute(self, sql, parameters=()):
        self.queries.append((sql, parameters))
        key = next(key for key in self.answers if key in sql)
        rows = self.answers[key]
        return type("Cursor", (), {"fetchone": lambda _: rows[0] if rows else None,
                                   "fetchall": lambda _: rows})()


class AccessTests(unittest.TestCase):
    def test_preview_is_for_owners_and_active_is_for_every_viewer(self):
        self.assertTrue(live_view.live_view_allowed(_access("preview"), OWNER))
        self.assertTrue(live_view.live_view_allowed(_access("preview"), dict(VIEWER, roles=["viewer", "platform_admin"])))
        self.assertFalse(live_view.live_view_allowed(_access("preview"), VIEWER))
        self.assertTrue(live_view.live_view_allowed(_access("active"), VIEWER))

    def test_closed_cases(self):
        for access, user in [
            (_access(), OWNER),  # switch never set
            (_access("disabled"), OWNER),
            (_access("blocked"), OWNER),
            (_access("active"), LEGACY_OWNER),  # allow-list or outage fallback
            (_access("active", subject_type="composite_portfolio"), OWNER),
            (_access("active", preview_mode=True), OWNER),
        ]:
            with self.subTest(access=access["feature_states"], user=user.get("authorization_source")):
                self.assertFalse(live_view.live_view_allowed(access, user))


class FreshnessTests(unittest.TestCase):
    def _status(self, minutes_ago=0, state="connected"):
        return {"observed_at": NOW - timedelta(minutes=minutes_ago), "gateway_state": state}

    def test_states(self):
        cases = [
            ((self._status(), NOW - timedelta(minutes=1), 0), "live"),
            ((self._status(), NOW - timedelta(minutes=1), 2), "delayed"),
            ((self._status(), NOW - timedelta(minutes=4), 0), "behind"),
            ((self._status(6), NOW - timedelta(minutes=1), 0), "paused"),
            ((self._status(state="disconnected"), NOW, 0), "paused"),
            ((None, None, 0), "unavailable"),
            ((self._status(), None, 0), "unavailable"),
        ]
        for (status, snapshot, delayed), expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(live_view.freshness(NOW, status, snapshot, delayed)["state"], expected)

    def test_polling_slows_when_markets_are_closed(self):
        self.assertEqual(live_view.freshness(NOW, None, None)["poll_seconds"], 30)
        weekend = live_view.freshness(WEEKEND, {"observed_at": WEEKEND, "gateway_state": "connected"},
                                      WEEKEND - timedelta(minutes=10))
        self.assertEqual((weekend["poll_seconds"], weekend["state"]), (300, "live"))


class EndpointTests(unittest.TestCase):
    def _patch(self, access, connection):
        @contextmanager
        def database():
            yield connection

        for patcher in (patch.object(web, "_portfolio_access_context", return_value=access),
                        patch.object(web, "_database", database)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_summary(self):
        connection = FakeConnection(
            status={"observed_at": datetime.now(timezone.utc), "gateway_state": "connected", "market_data_type": 3},
            summary={"observed_at": datetime.now(timezone.utc), "base_currency": "HKD",
                     "net_liquidation": Decimal("1250000.50"), "total_cash": Decimal("50000"),
                     "daily_pnl": Decimal("1234.5"), "unrealized_pnl": None, "realized_pnl": None,
                     "cash_by_currency": {"USD": 2564.1, "HKD": 30000}},
            delayed=1,
        )
        self._patch(_access("preview"), connection)
        result = web.live_summary("U1", user=OWNER)
        self.assertEqual(result["state"], "delayed")
        self.assertEqual(result["account"]["net_liquidation"], 1250000.5)
        self.assertEqual([x["currency"] for x in result["account"]["cash_by_currency"]], ["HKD", "USD"])
        self.assertTrue(all(parameters[:2] == (web.TENANT_ID, "U1") for _, parameters in connection.queries[1:]))

    def test_positions_and_trades(self):
        positions = [{"observed_at": NOW, "conid": "265598", "symbol": "AAPL", "description": "APPLE INC",
                      "sec_type": "STK", "currency": "USD", "quantity": Decimal("100"), "average_cost": Decimal("150.25"),
                      "market_price": Decimal("190.5"), "market_value": Decimal("19050"),
                      "unrealized_pnl": Decimal("4025"), "daily_pnl": Decimal("250"), "last_price": Decimal("190.5"),
                      "prior_close": Decimal("188"), "market_data_type": 3, "quote_observed_at": NOW}]
        trades = [{"exec_id": "e1", "executed_at": NOW, "symbol": "AAPL", "sec_type": "STK", "currency": "USD",
                   "side": "BUY", "quantity": Decimal("10"), "price": Decimal("190.1"), "commission": None,
                   "commission_currency": None, "realized_pnl": None, "confirmed": False}]
        connection = FakeConnection(positions=positions, trades=trades)
        self._patch(_access("active"), connection)
        result = web.live_positions("U1", user=VIEWER)
        row = result["positions"][0]
        self.assertEqual((row["price"], row["price_source"]), (190.5, "delayed"))
        self.assertAlmostEqual(row["change_pct"], 190.5 / 188 - 1)
        trade = web.live_trades("U1", user=VIEWER)["trades"][0]
        self.assertEqual((trade["quantity"], trade["confirmed"]), (10.0, False))
        sql, parameters = connection.queries[-1]
        self.assertIn("t.execution_id = e.exec_id", sql)
        self.assertEqual(parameters[3], live_view.MAX_TRADES)

    def test_not_allowed_never_reads_live_tables(self):
        connection = FakeConnection()
        self._patch(_access("preview"), connection)
        for endpoint in (web.live_summary, web.live_positions, web.live_trades):
            with self.subTest(endpoint=endpoint.__name__), self.assertRaises(HTTPException) as raised:
                endpoint("U1", user=VIEWER)
            self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(connection.queries, [])


class PageTests(unittest.TestCase):
    def _page(self, user, access, section="overview"):
        with patch.object(web, "_account_directory", return_value=[]):
            return web._render_account_page(user, account_id="U1", section=section, access=access)

    def test_panels_only_when_live_view_allows(self):
        page = self._page(OWNER, _access("preview"), "holdings")
        self.assertIn('"section": "holdings"', page)
        self.assertIn("/api/portfolio/${encodeURIComponent(config.account)}/live", page)
        self.assertIn(".live-badge.freshness", page)
        self.assertNotIn("live-badge", self._page(VIEWER, _access("preview")))
        self.assertNotIn("live-badge", self._page(OWNER, _access()))

    def test_page_assets_escape_the_account(self):
        assets = live_ui.live_assets("U1</script><script>alert(1)</script>", "overview")
        self.assertNotIn("U1</script>", assets)
        self.assertIn("\\u003c/script>", assets)


if __name__ == "__main__":
    unittest.main()
