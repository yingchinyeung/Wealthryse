"""The account Desk page (web_desk.py): its switch, who sees it, the live snapshot, missing views,
the pre-trade check, and the account page staying byte-for-byte the same while DESK_PAGE is unset.

The database tests at the end need a disposable PostgreSQL (AGENT_TEST_PG_ADMIN_DSN) and are
skipped otherwise; they create and drop their own database and roles.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ibkr_etl import live_view, metrics_guide, web, web_desk

ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 10, 13, 14, 35, tzinfo=timezone.utc)   # Tuesday, 10:35 in New York: US market open
OWNER = {"email": "owner@example.com", "object_id": "owner-1", "role": "owner", "roles": ["owner"],
         "authorization_source": "database"}
VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer", "roles": ["viewer"],
          "authorization_source": "database"}
ADMIN_VIEWER = dict(VIEWER, roles=["viewer", "platform_admin"])
LEGACY_OWNER = {"email": "owner@example.com", "object_id": "owner-1", "role": "owner"}
AAPL, MSFT, TENCENT, UNQUOTED = "265598", "272093", "11109", "999001"


def _access(state="active", **overrides):
    access = {"subject_id": "U1", "display_name": "Test account", "reporting_currency": "USD",
              "history_start_date": "2025-01-01", "subject_type": "physical_account",
              "base_currency": "USD", "lifecycle_status": "active", "preview_mode": False,
              "feature_states": {"website_analytics": "active"}}
    if state:
        access["feature_states"] = dict(access["feature_states"], live_view=state)
    access.update(overrides)
    return access


@contextmanager
def _desk_page(value):
    """DESK_PAGE set to ``value``, or unset for None, for the code that reads os.environ."""

    saved = os.environ.pop("DESK_PAGE", None)
    if value is not None:
        os.environ["DESK_PAGE"] = value
    try:
        yield
    finally:
        os.environ.pop("DESK_PAGE", None)
        if saved is not None:
            os.environ["DESK_PAGE"] = saved


# Synthetic history: 60 sessions of benchmark returns and marks moving with them.
def _business_days(end: date, count: int) -> list[str]:
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(days)


DAYS = _business_days(date(2026, 10, 12), 61)
BENCH = {day: 0.01 * math.sin(i * 1.7) + 0.002 * math.cos(i * 0.3) for i, day in enumerate(DAYS[1:], start=1)}


def _marks(conid: str, beta: float, start: float, wiggle: float) -> list[dict]:
    rows, price = [{"snapshot_date": DAYS[0], "conid": conid, "mark_price_text": str(start)}], start
    for i, day in enumerate(DAYS[1:], start=1):
        price *= 1.0 + beta * BENCH[day] + wiggle * math.sin(i * 2.9)
        rows.append({"snapshot_date": day, "conid": conid, "mark_price_text": f"{price:.6f}"})
    return rows


def _position(conid, symbol, currency, quantity, last, prior, *, quote_age=30.0, bid=None, ask=None,
              description=None):
    quoted = quote_age is not None
    return {
        "conid": conid, "symbol": symbol, "description": description, "sec_type": "STK", "currency": currency,
        "quantity": Decimal(str(quantity)), "market_price": Decimal(str(last)),
        "market_value": Decimal(str(quantity * last)),
        "last_price": Decimal(str(last)) if quoted else None,
        "bid_price": Decimal(str(bid)) if quoted and bid else None,
        "ask_price": Decimal(str(ask)) if quoted and ask else None,
        "prior_close": Decimal(str(prior)) if quoted else None, "market_data_type": 1 if quoted else None,
        "quote_observed_at": NOW - timedelta(seconds=quote_age) if quoted else None,
    }


SUMMARY = {"observed_at": NOW - timedelta(seconds=10), "base_currency": "USD",
           "net_liquidation": Decimal("100000"), "total_cash": Decimal("15000"), "daily_pnl": Decimal("121"),
           "cash_by_currency": {"HKD": 10000.0, "USD": 5000.0}}
POSITIONS = [
    _position(AAPL, "AAPL", "USD", 100, 102.0, 100.0, quote_age=30, bid=101.9, ask=102.1, description="APPLE INC"),
    _position(MSFT, "MSFT", "USD", 50, 198.0, 200.0, quote_age=60, bid=197.9, ask=198.1),
    # A Hong Kong holding whose quote last changed six hours ago: stale while New York trades.
    _position(TENCENT, "700", "HKD", 200, 410.0, 400.0, quote_age=6 * 3600, bid=409.8, ask=410.2),
    _position(UNQUOTED, "XYZ", "USD", 10, 50.0, 50.0, quote_age=None),
]
FILLS = [
    {"exec_id": "e1", "executed_at": NOW - timedelta(hours=1), "conid": MSFT, "symbol": "MSFT", "sec_type": "STK",
     "currency": "USD", "side": "BUY", "quantity": Decimal("20"), "price": Decimal("199")},
    # Before Monday's 16:00 New York close: yesterday's trade, already in the prior close.
    {"exec_id": "e0", "executed_at": datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc), "conid": MSFT,
     "symbol": "MSFT", "sec_type": "STK", "currency": "USD", "side": "BUY", "quantity": Decimal("5"),
     "price": Decimal("150")},
    # A currency conversion moves cash, not a holding.
    {"exec_id": "e2", "executed_at": NOW - timedelta(minutes=30), "conid": "12087792", "symbol": "USD.HKD",
     "sec_type": "CASH", "currency": "HKD", "side": "BUY", "quantity": Decimal("1000"), "price": Decimal("7.78")},
    # After the snapshot: not in its quantities yet.
    {"exec_id": "e3", "executed_at": NOW, "conid": AAPL, "symbol": "AAPL", "sec_type": "STK",
     "currency": "USD", "side": "BUY", "quantity": Decimal("7"), "price": Decimal("102")},
]
GSPC = {"benchmark_id": "GSPC", "symbol": "^GSPC", "name": "S&P 500", "reporting_currency": "USD"}
SPX = {"symbol": "SPX", "currency": "USD", "last_price": Decimal("5050"), "bid_price": None, "ask_price": None,
       "prior_close": Decimal("5000"), "market_data_type": 1, "observed_at": NOW - timedelta(seconds=20)}
FX = [
    {"rate_date": "2026-10-12", "from_currency": "HKD", "to_currency": "USD", "rate": 0.1285,
     "quality_status": "pass", "provider": "ecb"},
    {"rate_date": "2026-10-13", "from_currency": "HKD", "to_currency": "USD", "rate": 0.1286,
     "quality_status": "pass", "provider": "ecb"},
]
MARKS = _marks(AAPL, 1.2, 80.0, 0.004) + _marks(MSFT, 0.8, 180.0, 0.003) + _marks(TENCENT, 0.5, 350.0, 0.005)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    """Answers the Desk's queries with canned rows by the relation each one reads."""

    def __init__(self, *, summary=SUMMARY, positions=POSITIONS, views=None, unreadable=(), fills=FILLS,
                 benchmark=GSPC, benchmark_quote=SPX, marks=MARKS, fx=FX):
        self.views = {"subject_live_executions": "silver", "benchmark_live_quotes": "silver"} if views is None \
            else views
        self.unreadable = set(unreadable)
        self.answers = {
            "FROM subject_live_summary": [summary] if summary else [],
            "FROM subject_live_positions": list(positions),
            "subject_live_executions": list(fills),
            "FROM portfolio_benchmark_mapping": [benchmark] if benchmark else [],
            "FROM performance_comparison_daily": [{"observation_date": d, "benchmark_return": r}
                                                   for d, r in BENCH.items()],
            "benchmark_live_quotes": [benchmark_quote] if benchmark_quote else [],
            "FROM subject_position_series": list(marks),
            "FROM reference_fx_rates": list(fx),
        }
        self.queries = []
        self.savepoints = []

    def execute(self, sql, parameters=()):
        self.queries.append((sql, parameters))
        if "pg_catalog.pg_class" in sql:
            schema, name = parameters
            return Cursor([{"present": self.views.get(name) == schema}])
        key = next(key for key in self.answers if key in sql)
        if key in self.unreadable:
            raise PermissionError(f"permission denied for view {key}")
        return Cursor(self.answers[key])

    @contextmanager
    def transaction(self):
        self.savepoints.append("open")
        try:
            yield
        except Exception:
            self.savepoints[-1] = "rolled back"
            raise
        self.savepoints[-1] = "released"

    def asked(self, fragment):
        return [sql for sql, _ in self.queries if fragment in sql]


def _client(connection, *, access=None, user=OWNER, environ=None):
    app = FastAPI()
    calls = []

    def authorized_user():
        calls.append("auth")
        return user

    def access_context(account_id, who):
        calls.append(("access", account_id))
        return access or _access()

    @contextmanager
    def read_database():
        yield connection

    web_desk.install(app, authorized_user=authorized_user, access_context=access_context,
                     read_database=read_database, comparison_version=lambda subject, currency: "v1",
                     tenant_id=TENANT, now=lambda: NOW,
                     environ={"DESK_PAGE": "on"} if environ is None else environ)
    return TestClient(app), calls


def _snapshot(connection=None, **kwargs):
    client, _ = _client(connection or FakeConnection(), **kwargs)
    response = client.get("/api/portfolio/U1/desk")
    return response


class SwitchAndAccessTests(unittest.TestCase):
    def test_off_by_default_answers_404_before_sign_in(self):
        for environ in ({}, {"DESK_PAGE": ""}, {"DESK_PAGE": "off"}, {"DESK_PAGE": "yes"}):
            with self.subTest(environ=environ):
                connection = FakeConnection()
                client, calls = _client(connection, environ=environ)
                for response in (client.get("/api/portfolio/U1/desk"),
                                 client.post("/api/portfolio/U1/desk/pre-trade", json={"orders": []})):
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {"detail": "Not Found"})
                self.assertEqual(calls, [])
                self.assertEqual(connection.queries, [])
                self.assertEqual(web_desk.desk_mode(environ), "off")
                self.assertFalse(web_desk.desk_allowed(_access(), OWNER, environ))

    def test_unknown_route_answers_the_same_404(self):
        app = FastAPI()
        self.assertEqual(TestClient(app).get("/api/portfolio/U1/desk").json(), {"detail": "Not Found"})

    def test_owners_mode_shows_owners_and_platform_admins_only(self):
        environ = {"DESK_PAGE": "owners"}
        self.assertEqual(_snapshot(environ=environ).status_code, 200)
        self.assertEqual(_snapshot(environ=environ, user=ADMIN_VIEWER).status_code, 200)
        refused = _snapshot(environ=environ, user=VIEWER)
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(refused.json()["detail"], web_desk.NOT_AVAILABLE)
        # "on" opens it to everyone with the live view.
        self.assertEqual(_snapshot(environ={"DESK_PAGE": " ON "}, user=VIEWER).status_code, 200)

    def test_access_is_the_live_view_rule_plus_the_setting(self):
        accesses = [_access(None), _access("preview"), _access("active"), _access("disabled"),
                    _access("active", subject_type="composite_portfolio"), _access("active", preview_mode=True),
                    _access("preview", subject_type="composite_portfolio")]
        for access in accesses:
            for user in (OWNER, VIEWER, ADMIN_VIEWER, LEGACY_OWNER):
                live = live_view.live_view_allowed(access, user)
                roles = set(user.get("roles") or ()) | {user.get("role", "")}
                with self.subTest(access=access["feature_states"], type=access["subject_type"], user=user):
                    self.assertEqual(web_desk.desk_allowed(access, user, {"DESK_PAGE": "on"}), live)
                    self.assertEqual(web_desk.desk_allowed(access, user, {"DESK_PAGE": "owners"}),
                                     live and bool(roles & live_view.PREVIEW_ROLES))
                    self.assertFalse(web_desk.desk_allowed(access, user, {}))
        # Through the route: a viewer on a preview account and a legacy sign-in get the live view's 404.
        self.assertEqual(_snapshot(access=_access("preview"), user=VIEWER).status_code, 404)
        self.assertEqual(_snapshot(access=_access("active"), user=LEGACY_OWNER).status_code, 404)
        self.assertEqual(_snapshot(access=_access("preview"), user=OWNER).status_code, 200)

    def test_composites_are_refused_with_a_clear_409(self):
        composite = _access("active", subject_type="composite_portfolio")
        connection = FakeConnection()
        client, _ = _client(connection, access=composite)
        for response in (client.get("/api/portfolio/U1/desk"),
                         client.post("/api/portfolio/U1/desk/pre-trade",
                                     json={"orders": [{"symbol": "AAPL", "side": "BUY", "quantity": 1}]})):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"], web_desk.COMPOSITE_MESSAGE)
        self.assertEqual(connection.queries, [])


class LimitSettingTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        limits, settings, source = web_desk.desk_limits({})
        self.assertEqual((limits, source), (web_desk.DEFAULT_LIMITS, "defaults"))
        self.assertEqual(settings, {"max_quote_age_seconds": 900.0, "amber_at": 0.8})
        limits, settings, source = web_desk.desk_limits({"DESK_LIMITS": (
            '{"gross_exposure": 1.3, "stale_quote_share": null, "factor_exposures": {"*": 1.5},'
            ' "max_quote_age_seconds": 600, "amber_at": 0.9}')})
        self.assertEqual(source, "DESK_LIMITS")
        self.assertEqual(limits["gross_exposure"], 1.3)
        self.assertNotIn("stale_quote_share", limits)
        self.assertEqual(limits["factor_exposures"], {"*": 1.5})
        self.assertEqual(settings, {"max_quote_age_seconds": 600.0, "amber_at": 0.9})

    def test_an_unreadable_setting_keeps_every_default(self):
        for raw in ("not json", "[1, 2]", '{"var_1d": [0.1, 0.0]}', '{"var_1d": -1}', '{"net_exposure": true}',
                    '{"amber_at": 2}', '{"max_quote_age_seconds": 0}', '{"max_quote_age_seconds": "x"}'):
            with self.subTest(raw=raw), self.assertLogs("uvicorn.error.desk", "WARNING"):
                limits, settings, source = web_desk.desk_limits({"DESK_LIMITS": raw})
                self.assertEqual((limits, settings), (web_desk.DEFAULT_LIMITS, web_desk.DEFAULT_SETTINGS))
                self.assertEqual(source, "defaults: DESK_LIMITS could not be read")

    def test_prior_close_is_the_market_close_before_today(self):
        tuesday = datetime(2026, 10, 13, 14, 35, tzinfo=timezone.utc)
        self.assertEqual(web_desk.prior_close_time("USD", tuesday), datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(web_desk.prior_close_time("HKD", tuesday), datetime(2026, 10, 12, 8, 10, tzinfo=timezone.utc))
        monday = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(web_desk.prior_close_time("USD", monday), datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(web_desk.prior_close_time("CNH", monday), datetime(2026, 10, 9, 8, 10, tzinfo=timezone.utc))

    def test_fx_table_takes_direct_then_inverted_daily_rates_no_older_than_four_days(self):
        rows = FX + [
            {"rate_date": "2026-10-13", "from_currency": "USD", "to_currency": "EUR", "rate": 0.8,
             "quality_status": "pass", "provider": "ecb"},
            {"rate_date": "2026-10-12", "from_currency": "USD", "to_currency": "EUR", "rate": 0.81,
             "quality_status": "warn", "provider": "ecb"},
            {"rate_date": "2026-10-13", "from_currency": "HKD", "to_currency": "USD", "rate": 0.2,
             "quality_status": "warn", "provider": "other"},
            {"rate_date": "2026-10-01", "from_currency": "JPY", "to_currency": "USD", "rate": 0.0067,
             "quality_status": "pass", "provider": "ecb"},
            {"rate_date": "2026-10-13", "from_currency": "JPY", "to_currency": "USD", "rate": 0.0068,
             "quality_status": "pass", "provider": "ecb"},
        ]
        table = web_desk.fx_table(rows, ["HKD", "EUR", "JPY"], "USD", date(2026, 10, 13))
        self.assertEqual(table["HKD"], {"rate": 0.1286, "prior_close": 0.1285, "rate_date": "2026-10-13",
                                        "prior_date": "2026-10-12"})
        self.assertAlmostEqual(table["EUR"]["rate"], 1 / 0.8)
        self.assertAlmostEqual(table["EUR"]["prior_close"], 1 / 0.81)
        self.assertNotIn("JPY", table)   # its prior rate is twelve days old


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        response = _snapshot(self.connection)
        self.assertEqual(response.status_code, 200)
        self.body = response.json()
        self.snapshot = self.body["results"]["desk_snapshot"]
        self.holdings = {row["key"]: row for row in self.snapshot["pnl"]["holdings"]}

    def test_known_intraday_pnl_and_its_parts(self):
        body, total = self.body, self.snapshot["pnl"]["total"]
        self.assertTrue(body["available"])
        self.assertEqual((body["model_id"], body["model_version"]), ("desk-risk", "desk-risk-v1"))
        self.assertEqual(body["as_of"], (NOW - timedelta(seconds=30)).isoformat())   # the newest quote
        # AAPL 100 × (102 − 100) = 200; MSFT 50 × (198 − 200) + 20 bought today at 199 × (200 − 199) = −80;
        # HKD cash 10,000 × (0.1286 − 0.1285) = 1. The stale and unquoted holdings are left out.
        self.assertAlmostEqual(self.holdings[AAPL]["pnl_reporting"], 200.0)
        self.assertAlmostEqual(self.holdings[MSFT]["pnl_reporting"], -80.0)
        self.assertAlmostEqual(self.holdings[MSFT]["trading_pnl_reporting"], 20.0)
        self.assertAlmostEqual(total["cash_fx_pnl"], 1.0)
        self.assertAlmostEqual(total["pnl"], 121.0)
        self.assertEqual(body["inputs"]["fills_today"], 1)   # not yesterday's, the conversion, or after the snapshot
        nav = 100000.0 - 121.0
        self.assertAlmostEqual(body["inputs"]["nav"]["prior_close"], nav)
        self.assertAlmostEqual(self.snapshot["pnl_return"], 121.0 / nav)
        self.assertEqual(self.snapshot["metrics"]["pnl_return"], self.snapshot["pnl_return"])

    def test_the_explain_adds_up_and_the_market_part_is_beta_times_the_benchmark(self):
        explain = self.snapshot["explain"]
        self.assertTrue(explain["available"])
        self.assertEqual(explain["market_factor"], "Market")
        amounts, reconciliation = explain["amounts"], explain["reconciliation"]
        parts = amounts["Market"] + amounts["FX"] + amounts["Stock-specific"]
        self.assertAlmostEqual(parts + reconciliation["trading_pnl"] + reconciliation["cash_fx_pnl"], 121.0)
        self.assertAlmostEqual(reconciliation["difference"], 0.0, places=9)
        betas = self.body["inputs"]["betas"]["by_holding"]
        self.assertGreater(betas[AAPL], betas[MSFT])
        # Both open at 10,000 (AAPL 100 × 100, MSFT 50 × 200); the S&P 500 is up 1% (5,050 ÷ 5,000).
        self.assertAlmostEqual(amounts["Market"], 10000 * 0.01 * (betas[AAPL] + betas[MSFT]))
        self.assertAlmostEqual(self.body["inputs"]["benchmark"]["return_today"], 0.01)
        weights = self.snapshot["exposures"]["weights"]
        self.assertAlmostEqual(self.body["inputs"]["betas"]["portfolio_beta"],
                               sum(weights[k] * betas[k] for k in weights if k in betas))

    def test_stale_and_missing_quotes_are_flagged_not_filled_in(self):
        stale, unquoted = self.holdings[TENCENT], self.holdings[UNQUOTED]
        self.assertEqual(stale["status"], "stale")
        self.assertAlmostEqual(stale["quote_age_seconds"], 6 * 3600 - 30)
        self.assertIsNone(stale["pnl_reporting"])
        self.assertEqual(unquoted["status"], "no_quote")
        self.assertEqual(self.holdings[AAPL]["quote_age_seconds"], 0.0)
        self.assertEqual(self.holdings[MSFT]["quote_age_seconds"], 30.0)
        self.assertEqual({row["key"] for row in self.snapshot["pnl"]["excluded"]}, {TENCENT, UNQUOTED})
        # A stale holding keeps its last price in the exposures.
        self.assertIn(TENCENT, self.snapshot["exposures"]["valued_at_stale_prices"])
        checks = {c["metric"]: c for c in self.snapshot["limits"]["checks"]}
        self.assertAlmostEqual(checks["stale_quote_share"]["value"], 0.5)
        self.assertEqual(checks["stale_quote_share"]["status"], "red")
        self.assertEqual(self.snapshot["freshness"]["stale"], 1)
        self.assertEqual(self.snapshot["freshness"]["missing"], 1)

    def test_every_live_metric_is_checked_against_its_limit(self):
        checks = {c["metric"]: c for c in self.snapshot["limits"]["checks"]}
        self.assertEqual(set(checks), set(web_desk.DEFAULT_LIMITS))
        nav = 100000.0 - 121.0
        gross = (100 * 102 + 50 * 198 + 200 * 410 * 0.1286) / nav
        self.assertAlmostEqual(checks["gross_exposure"]["value"], gross)
        self.assertAlmostEqual(checks["largest_position_weight"]["value"], 200 * 410 * 0.1286 / nav)
        self.assertEqual(checks["gross_exposure"]["status"], "green")
        self.assertEqual(checks["pnl_return"]["limit_type"], "range")
        self.assertGreater(checks["var_1d"]["value"], 0.0)
        self.assertEqual(self.body["inputs"]["var"]["level"], 0.99)
        self.assertEqual(self.body["inputs"]["var"]["no_history"], [])
        self.assertEqual(self.snapshot["limits"]["status"], "red")
        self.assertEqual([b["metric"] for b in self.snapshot["limits"]["breaches"]], ["stale_quote_share"])
        self.assertEqual(self.body["inputs"]["limits_source"], "defaults")

    def test_reads_silver_views_only_and_says_nothing_is_missing(self):
        self.assertEqual(self.body["missing"], [])
        for sql, _ in self.connection.queries:
            self.assertNotRegex(sql, r"\blive_(executions|quotes|positions|account_values|gateway_status|contracts)\b")
        self.assertEqual(len(self.connection.asked("FROM silver.subject_live_executions")), 1)
        self.assertEqual(len(self.connection.asked("FROM silver.benchmark_live_quotes")), 1)
        self.assertEqual(self.connection.savepoints, ["released", "released"])

    def test_limits_from_the_setting_and_the_quote_age_limit(self):
        environ = {"DESK_PAGE": "on",
                   "DESK_LIMITS": '{"stale_quote_share": null, "max_quote_age_seconds": 30000}'}
        body = _snapshot(environ=environ).json()
        snapshot = body["results"]["desk_snapshot"]
        holdings = {row["key"]: row for row in snapshot["pnl"]["holdings"]}
        self.assertEqual(holdings[TENCENT]["status"], "ok")   # six hours is inside 30,000 seconds
        self.assertNotIn("stale_quote_share", {c["metric"] for c in snapshot["limits"]["checks"]})
        self.assertEqual(body["inputs"]["limits_source"], "DESK_LIMITS")
        self.assertEqual(body["inputs"]["max_quote_age_seconds"], 30000.0)

    def test_no_live_snapshot_yet(self):
        response = _snapshot(FakeConnection(summary=None))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["reason"], web_desk.NO_SNAPSHOT)
        self.assertEqual(body["missing"][-1]["code"], "no_live_snapshot")


class MissingViewTests(unittest.TestCase):
    def test_before_desk_live_views_the_desk_works_and_says_what_is_missing(self):
        connection = FakeConnection(views={})
        response = _snapshot(connection)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        snapshot = body["results"]["desk_snapshot"]
        codes = [m["code"] for m in body["missing"]]
        self.assertIn("executions_view_missing", codes)
        self.assertIn("benchmark_view_missing", codes)
        self.assertTrue(all("desk_live_views" in m["message"] for m in body["missing"]
                            if m["code"].endswith("view_missing")))
        self.assertIn("No fills today", body["missing"][codes.index("executions_view_missing")]["message"])
        self.assertIn("No market factor", body["missing"][codes.index("benchmark_view_missing")]["message"])
        self.assertIsNone(body["inputs"]["fills_today"])
        # Without the fill, MSFT is measured from the prior close: 200 − 100 + 1.
        self.assertAlmostEqual(snapshot["pnl"]["total"]["pnl"], 101.0)
        explain = snapshot["explain"]
        self.assertTrue(explain["available"])
        self.assertIsNone(explain["market_factor"])
        self.assertNotIn("Market", explain["amounts"])
        self.assertAlmostEqual(explain["reconciliation"]["difference"], 0.0, places=9)
        self.assertEqual(connection.asked("subject_live_executions"), [])
        self.assertEqual(connection.asked("benchmark_live_quotes"), [])
        self.assertEqual(connection.savepoints, [])

    def test_public_compatibility_views_are_read_when_silver_has_none(self):
        connection = FakeConnection(views={"subject_live_executions": "public", "benchmark_live_quotes": "public"})
        body = _snapshot(connection).json()
        self.assertEqual(body["missing"], [])
        self.assertEqual(len(connection.asked("FROM public.subject_live_executions")), 1)

    def test_a_view_that_cannot_be_read_rolls_back_its_savepoint_only(self):
        connection = FakeConnection(unreadable={"subject_live_executions", "benchmark_live_quotes"})
        with self.assertLogs("uvicorn.error.desk", "WARNING"):
            response = _snapshot(connection)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        codes = [m["code"] for m in body["missing"]]
        self.assertIn("executions_view_unreadable", codes)
        self.assertIn("benchmark_view_unreadable", codes)
        self.assertEqual(connection.savepoints, ["rolled back", "rolled back"])
        # The reads after the failed ones still ran in the same transaction.
        self.assertEqual(len(connection.asked("FROM subject_position_series")), 1)
        self.assertEqual(len(connection.asked("FROM reference_fx_rates")), 1)
        self.assertTrue(body["available"])

    def test_no_primary_benchmark_or_a_stale_benchmark_quote_means_no_market_factor(self):
        body = _snapshot(FakeConnection(benchmark=None)).json()
        self.assertIn("no_primary_benchmark", [m["code"] for m in body["missing"]])
        self.assertIsNone(body["results"]["desk_snapshot"]["explain"]["market_factor"])
        old = dict(SPX, observed_at=NOW - timedelta(hours=2))
        body = _snapshot(FakeConnection(benchmark_quote=old)).json()
        self.assertIn("benchmark_quote_stale", [m["code"] for m in body["missing"]])
        self.assertIsNone(body["results"]["desk_snapshot"]["explain"]["market_factor"])
        body = _snapshot(FakeConnection(benchmark_quote=None)).json()
        self.assertIn("benchmark_quote_missing", [m["code"] for m in body["missing"]])

    def test_missing_fx_and_history_are_reported(self):
        body = _snapshot(FakeConnection(fx=[], marks=[])).json()
        codes = [m["code"] for m in body["missing"]]
        self.assertIn("no_fx_rate_HKD", codes)
        self.assertIn("history_short", codes)
        self.assertIn("betas_unavailable", codes)
        holdings = {row["key"]: row for row in body["results"]["desk_snapshot"]["pnl"]["holdings"]}
        self.assertEqual(holdings[TENCENT]["status"], "stale")
        checks = {c["metric"] for c in body["results"]["desk_snapshot"]["limits"]["checks"]}
        self.assertNotIn("var_1d", checks)
        self.assertIn("var_1d", body["results"]["desk_snapshot"]["limits"]["missing_metrics"])


class PreTradeTests(unittest.TestCase):
    def _post(self, body, connection=None, **kwargs):
        client, _ = _client(connection or FakeConnection(), **kwargs)
        return client.post("/api/portfolio/U1/desk/pre-trade", json=body)

    def test_validation(self):
        good = {"symbol": "AAPL", "side": "BUY", "quantity": 10}
        for body in ({}, {"orders": []}, {"orders": [good] * 21}, {"orders": [dict(good, venue="X")]},
                     {"orders": [good], "send": True}, {"orders": [dict(good, quantity=0)]},
                     {"orders": [dict(good, quantity=-5)]}, {"orders": [dict(good, side="buy")]},
                     {"orders": [{"side": "BUY", "quantity": 1}]}, {"orders": [dict(good, conid="12ab")]},
                     {"orders": [dict(good, limit_price=0)]}, {"orders": [dict(good, symbol="<script>")]}):
            with self.subTest(body=body):
                self.assertEqual(self._post(body).status_code, 422)
        client, _ = _client(FakeConnection())
        form = client.post("/api/portfolio/U1/desk/pre-trade", data={"orders": "AAPL"})
        self.assertEqual(form.status_code, 422)

    def test_result_is_hypothetical_on_the_live_positions(self):
        response = self._post({"orders": [
            {"symbol": "aapl", "side": "BUY", "quantity": 10},
            {"conid": TENCENT, "side": "SELL", "quantity": 100, "limit_price": 420},
            {"symbol": "NVDA", "side": "BUY", "quantity": 5, "limit_price": 120},
            {"symbol": "TSLA", "side": "BUY", "quantity": 5},
        ]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["hypothetical"])
        self.assertIn("Nothing is placed or sent to IBKR", body["notice"])
        self.assertEqual((body["model_id"], body["model_version"]), ("pre-trade", "pre-trade-v1"))
        check = body["results"]["pre_trade_check"]
        orders = {o["holding"]: o for o in check["orders"]}
        self.assertEqual(set(orders), {AAPL, TENCENT, "new:NVDA"})
        self.assertEqual([o["holding"] for o in check["refused_orders"]], ["new:TSLA"])
        self.assertEqual(check["unpriced_positions"], [UNQUOTED])
        self.assertAlmostEqual(body["nav"], 100000.0)
        # AAPL at its live mark, the Hong Kong limit in HKD at today's rate, the new name at its limit.
        self.assertAlmostEqual(orders[AAPL]["value"], 1020.0)
        self.assertAlmostEqual(orders[TENCENT]["price"], 420 * 0.1286)
        self.assertAlmostEqual(orders["new:NVDA"]["value"], 600.0)
        before = (100 * 102 + 50 * 198 + 200 * 410 * 0.1286) / 100000
        self.assertAlmostEqual(check["before"]["gross_exposure"], before)
        # Half the live spread: (102.1 − 101.9) ÷ 102 ÷ 2 × 1,020 = 1; impact needs volumes, not in silver.
        self.assertAlmostEqual(orders[AAPL]["spread_cost"], 1.0)
        self.assertIsNone(orders[AAPL]["impact_cost"])
        self.assertIn("impact", orders[AAPL]["missing_cost_data"])
        self.assertEqual(orders["new:NVDA"]["missing_cost_data"], ["spread", "impact"])
        self.assertIn(web_desk.COST_NOTE, body["notes"])
        self.assertEqual(check["var_level"], 0.99)
        self.assertEqual(check["risk_model"], "the covariance given")   # Ledoit-Wolf of the year of marks
        self.assertEqual(check["history_days"], 60)
        self.assertIsNotNone(check["after"]["factor_exposures"]["Market"])
        self.assertEqual(check["limits"]["status_before"], "green")
        self.assertEqual(body["labels"]["new:NVDA"], "NVDA")
        self.assertNotIn("stale_quote_share", {c["metric"] for c in check["limits"]["after"]["checks"]})

    def test_an_order_that_breaks_a_limit_is_marked_new(self):
        body = self._post({"orders": [{"symbol": "MSFT", "side": "BUY", "quantity": 400}]}).json()
        limits = body["results"]["pre_trade_check"]["limits"]
        self.assertEqual(limits["status_after"], "red")
        self.assertEqual([b["metric"] for b in limits["new_breaches"]], ["largest_position_weight"])

    def test_a_symbol_two_holdings_share_needs_its_conid(self):
        positions = POSITIONS + [_position("777", "AAPL", "USD", 5, 102.0, 100.0)]
        response = self._post({"orders": [{"symbol": "AAPL", "side": "SELL", "quantity": 1}]},
                              FakeConnection(positions=positions))
        self.assertEqual(response.status_code, 400)
        self.assertIn("give its conid", response.json()["detail"])
        response = self._post({"orders": [{"conid": "777", "side": "SELL", "quantity": 1}]},
                              FakeConnection(positions=positions))
        self.assertEqual(response.status_code, 200)

    def test_engine_refusals_and_no_snapshot(self):
        response = self._post({"orders": [{"symbol": "TSLA", "side": "BUY", "quantity": 1}]})
        self.assertEqual(response.status_code, 422)
        self.assertIn("None of the orders can be checked", response.json()["detail"])
        response = self._post({"orders": [{"symbol": "AAPL", "side": "BUY", "quantity": 1}]},
                              FakeConnection(summary=None))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], web_desk.NO_SNAPSHOT)

    def test_owners_mode_and_off_apply_to_the_check_too(self):
        body = {"orders": [{"symbol": "AAPL", "side": "BUY", "quantity": 1}]}
        self.assertEqual(self._post(body, environ={"DESK_PAGE": "owners"}, user=VIEWER).status_code, 404)
        self.assertEqual(self._post(body, environ={"DESK_PAGE": "owners"}).status_code, 200)
        self.assertEqual(self._post(body, environ={}).json(), {"detail": "Not Found"})


class VolumeImpactTests(unittest.TestCase):
    """Once silver daily_volumes exists (phase 3), the pre-trade check estimates market impact."""

    # Under each holding's conid, as the holdings' own fetch stores them.
    VOLUMES = ([{"instrument_id": AAPL, "price_date": day, "volume": 1_000_000.0} for day in DAYS[-25:]]
               + [{"instrument_id": TENCENT, "price_date": day, "volume": 0.0 if i % 5 else 20_000_000.0}
                  for i, day in enumerate(DAYS[-30:])])
    ORDERS = {"orders": [{"symbol": "AAPL", "side": "BUY", "quantity": 10},
                         {"symbol": "MSFT", "side": "SELL", "quantity": 5}]}

    def _connection(self, volumes=True):
        views = {"subject_live_executions": "silver", "benchmark_live_quotes": "silver"}
        if volumes:
            views["daily_volumes"] = "silver"
        connection = FakeConnection(views=views)
        connection.answers["FROM daily_volumes"] = list(self.VOLUMES)
        return connection

    def _post(self, connection):
        client, _ = _client(connection)
        response = client.post("/api/portfolio/U1/desk/pre-trade", json=self.ORDERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_with_volumes_the_impact_is_estimated_for_the_holdings_that_have_them(self):
        connection = self._connection()
        body = self._post(connection)
        orders = {o["holding"]: o for o in body["results"]["pre_trade_check"]["orders"]}
        aapl = orders[AAPL]
        self.assertEqual(aapl["average_daily_volume"], 1_000_000.0)
        self.assertAlmostEqual(aapl["impact"], aapl["daily_volatility"] * math.sqrt(10 / 1_000_000), places=15)
        self.assertAlmostEqual(aapl["impact_cost"], aapl["impact"] * 1020.0, places=12)
        self.assertAlmostEqual(aapl["cost"], aapl["spread_cost"] + aapl["impact_cost"], places=12)
        self.assertEqual(aapl["missing_cost_data"], [])
        # MSFT has no volumes in silver: half the spread only, as before.
        self.assertIsNone(orders[MSFT]["impact_cost"])
        self.assertEqual(orders[MSFT]["missing_cost_data"], ["impact"])
        self.assertNotIn(web_desk.COST_NOTE, body["notes"])
        self.assertIn(web_desk.VOLUME_COST_NOTE + " 2 of 4 holdings have volume data.", body["notes"])
        # One read: each live holding's conid, the 45 days to today, zeros (holidays) left out.
        (sql, parameters), = [(s, p) for s, p in connection.queries if "FROM daily_volumes" in s]
        self.assertEqual(parameters, (TENANT, sorted([TENCENT, AAPL, MSFT, UNQUOTED]), "2026-08-29", "2026-10-13"))
        self.assertIn("volume > 0", sql)
        self.assertNotIn("market_data_prices", sql)
        self.assertEqual(connection.savepoints[-1], "released")

    def test_without_the_table_the_check_is_unchanged(self):
        with_table = self._post(self._connection())
        connection = self._connection(volumes=False)
        body = self._post(connection)
        self.assertEqual(connection.asked("FROM daily_volumes"), [])
        self.assertIn(web_desk.COST_NOTE, body["notes"])
        orders = {o["holding"]: o for o in body["results"]["pre_trade_check"]["orders"]}
        self.assertIsNone(orders[AAPL]["impact_cost"])
        # Only the liquidity figures, which need volumes, differ.
        liquidity = {"liquidity_days", "value_weighted_liquidity_days", "sellable_within", "no_volume_data"}
        before, volumes = body["results"]["pre_trade_check"]["before"], with_table["results"]["pre_trade_check"]["before"]
        self.assertEqual({k: v for k, v in before.items() if k not in liquidity},
                         {k: v for k, v in volumes.items() if k not in liquidity})
        self.assertEqual((before["liquidity_days"], volumes["no_volume_data"]), (None, [MSFT]))

    def test_the_live_snapshot_never_reads_volumes(self):
        connection = self._connection()
        self.assertEqual(_snapshot(connection).status_code, 200)
        self.assertEqual(connection.asked("daily_volumes"), [])


class AccountPageTests(unittest.TestCase):
    def _page(self, user, access, section="overview", part=None):
        with patch.object(web, "_account_directory", return_value=[]):
            return web._render_account_page(user, account_id="U1", section=section, access=access, part=part)

    def test_with_desk_page_unset_the_account_page_is_byte_for_byte_unchanged(self):
        without = {key: label for key, label in web.ACCOUNT_TABS.items() if key != "desk"}
        # The quant sections have their own switch; keep them off so only the Desk is compared.
        with _desk_page(None), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUANT_ANALYTICS_PAGES", None)
            for user in (OWNER, VIEWER):
                for state in (None, "preview", "active"):
                    for kind in ("physical_account", "composite_portfolio"):
                        for section in without:
                            access = _access(state, subject_type=kind)
                            with self.subTest(user=user["role"], state=state, kind=kind, section=section):
                                page = self._page(user, access, section)
                                with patch.object(web, "ACCOUNT_TABS", without), \
                                        patch.object(web, "ALL_PAGE_SECTIONS", without | web.PLATFORM_SECTIONS):
                                    self.assertEqual(page, self._page(user, access, section))
                                self.assertNotIn("/desk\"", page)
                                self.assertNotIn('data-section="desk"', page)
                                self.assertNotIn("metric-intraday-p-l", page)

    def test_the_desk_tab_waits_for_its_register_entry(self):
        # The certified figures register places every ACCOUNT_SECTIONS page. The Desk joins
        # ACCOUNT_SECTIONS in the register release that adds it to out_of_scope_pages.
        self.assertNotIn("desk", web.ACCOUNT_SECTIONS)
        keys = list(web.ACCOUNT_TABS)
        self.assertEqual(keys.index("desk"), keys.index("modelling") + 1)
        self.assertEqual([key for key in keys if key != "desk"], list(web.ACCOUNT_SECTIONS))

    def test_with_desk_page_unset_the_desk_page_is_an_unknown_page(self):
        with _desk_page(None), patch.object(web, "_portfolio_access_context",
                                            side_effect=AssertionError("not reached")):
            with self.assertRaises(HTTPException) as raised:
                web._account_page("U1", "desk", OWNER, None)
        self.assertEqual((raised.exception.status_code, raised.exception.detail), (404, "Account page not found"))

    def test_the_desk_tab_and_page_where_the_setting_allows(self):
        with _desk_page("on"):
            page = self._page(OWNER, _access("active"))
            self.assertIn('href="/accounts/U1/desk">Desk</a>', page)
            desk = self._page(VIEWER, _access("active"), "desk")
            self.assertIn('<div class="page-view active" data-section="desk">', desk)
            self.assertIn("<title>Desk ·", desk)
            self.assertIn("nothing is placed or sent to IBKR", desk)
            self.assertIn('id="desk-pretrade"', desk)
            self.assertNotIn('<div class="page-view active" data-section="overview">', desk)
            # Elsewhere the Desk adds only its tab.
            self.assertNotIn('data-section="desk"', page)
            for access, user in ((_access("preview"), VIEWER), (_access("active"), LEGACY_OWNER),
                                 (_access("active", subject_type="composite_portfolio"), OWNER),
                                 (_access(None), OWNER)):
                with self.subTest(access=access["feature_states"], user=user.get("authorization_source")):
                    self.assertNotIn("/accounts/U1/desk", self._page(user, access))
                    with patch.object(web, "_portfolio_access_context", return_value=access), \
                            self.assertRaises(HTTPException) as raised:
                        web._account_page("U1", "desk", user, None)
                    self.assertEqual(raised.exception.status_code, 404)
        with _desk_page("owners"):
            self.assertIn("/accounts/U1/desk", self._page(OWNER, _access("active")))
            self.assertNotIn("/accounts/U1/desk", self._page(VIEWER, _access("active")))

    def test_page_config_cannot_break_out_of_its_script(self):
        view = web_desk.page_view("U1</script><script>alert(1)</script>", "USD")
        self.assertNotIn("U1</script>", view)
        self.assertIn("\\u003c/script>", view)


class GuideTests(unittest.TestCase):
    def test_every_desk_figure_links_to_a_desk_definition(self):
        names = [metric.name for metric in metrics_guide.DESK_DEFINITIONS]
        self.assertEqual(sorted(web_desk.GUIDE_NAMES), sorted(names))
        self.assertEqual(set(metrics_guide.DESK_CLIENT_CALCULATIONS), set(names))
        anchors = {metrics_guide.metric_anchor(name) for name in names}
        page = web_desk.page_view("U1", "USD")
        linked = set(re.findall(r'/metrics-guide#([a-z0-9-]+)', page))
        self.assertTrue(linked)
        self.assertEqual(sorted(linked - anchors), [])
        for name, _entry in web_desk.LIMIT_LABELS.values():
            self.assertIn(_entry, names)

    def test_with_desk_page_unset_the_guide_does_not_describe_the_desk(self):
        if metrics_guide.desk_guide_shown():
            self.skipTest("DESK_PAGE is set in this process")
        self.assertNotIn("desk", [section.key for section in metrics_guide.METRIC_SECTIONS])
        for metric in metrics_guide.DESK_DEFINITIONS:
            self.assertNotIn(metric.name, metrics_guide.METRIC_ANCHORS)
            self.assertNotIn(metric.name, metrics_guide.CLIENT_CALCULATIONS)

    def test_with_desk_page_on_the_guide_places_the_desk_after_modelling(self):
        script = (
            "import re\n"
            "from ibkr_etl import metrics_guide as g, web_desk as d\n"
            "from ibkr_etl.web import ACCOUNT_TABS\n"
            "keys = [s.key for s in g.METRIC_SECTIONS]\n"
            "assert keys.index('desk') == keys.index('modelling-portfolio') + 1, keys\n"
            "names = [m.name for s in g.METRIC_SECTIONS for m in s.metrics]\n"
            "assert len(names) == len(set(names)) and set(names) == set(g.CLIENT_CALCULATIONS)\n"
            "assert len(g.METRIC_ANCHORS) == len(names)\n"
            "anchors = set(g.METRIC_ANCHORS.values())\n"
            "linked = set(re.findall(r'/metrics-guide#([a-z0-9-]+)', d.page_view('U1', 'USD')))\n"
            "assert linked <= anchors, linked - anchors\n"
            "for name in d.GUIDE_NAMES:\n"
            "    assert g.metric_location(name) in ACCOUNT_TABS.values(), name\n"
            "    assert g.search(name)[0]['name'] == name, name\n"
            "    assert g.definition_payload(name)['link'] == d.GUIDE[name]\n"
            "page = g.render_metrics_guide()\n"
            "assert 'id=\"guide-desk\"' in page and page.count('class=\"metric-copy\"') == len(anchors)\n"
            "assert ('desk-risk-v1', 'Desk live P&L, explain and limits') in g.METHODOLOGY_VERSIONS\n"
            "print('ok', len(names))\n"
        )
        env = dict(os.environ, DESK_PAGE="on", PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env,
                                cwd=ROOT, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertTrue(result.stdout.startswith("ok "), result.stdout)


# The real database: the views missing, present but not granted, and granted ---------------------

ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
TOLERATED_MIGRATIONS = {"027_legacy_external_market_data_approval.sql", "056_specialist_audit_ledger.sql"}
TEST_PASSWORD = "desk-test-only-password"  # throwaway roles in a disposable test server
DESK_VIEWS_DRAFT = ROOT / "infra" / "medallion" / "drafts" / "desk_live_views.sql"
# The two views as desk_live_views defines them, for a tree that does not have the draft yet.
FALLBACK_VIEWS = """
CREATE VIEW silver.subject_live_executions WITH (security_invoker = true) AS
SELECT e.tenant_id, e.account_id AS subject_id, e.exec_id, e.executed_at, e.conid, e.symbol, e.sec_type,
       e.currency, e.side, e.quantity, e.price, e.commission, e.commission_currency, e.realized_pnl
FROM bronze.live_executions e;
CREATE VIEW silver.benchmark_live_quotes WITH (security_invoker = true) AS
SELECT c.tenant_id, c.conid, c.symbol, q.currency, q.last_price, q.bid_price, q.ask_price, q.prior_close,
       q.market_data_type, q.observed_at
FROM bronze.live_contracts c
CROSS JOIN LATERAL (SELECT * FROM bronze.live_quotes quote WHERE quote.tenant_id = c.tenant_id
                    AND quote.conid = c.conid ORDER BY quote.observed_at DESC LIMIT 1) q
WHERE c.is_benchmark;
CREATE VIEW public.subject_live_executions WITH (security_invoker = true) AS SELECT * FROM silver.subject_live_executions;
CREATE VIEW public.benchmark_live_quotes WITH (security_invoker = true) AS SELECT * FROM silver.benchmark_live_quotes;
"""


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class DeskDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        suffix = uuid.uuid4().hex[:8]
        cls.database, cls.owner, cls.web = f"desk_test_{suffix}", f"desk_owner_{suffix}", f"desk_web_{suffix}"
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {cls.owner} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"CREATE ROLE {cls.web} LOGIN PASSWORD '{TEST_PASSWORD}'")
            admin.execute(f"ALTER ROLE {cls.web} SET default_transaction_read_only = on")
            admin.execute(f'CREATE DATABASE "{cls.database}" OWNER {cls.owner}')
        with psycopg.connect(cls._dsn(), autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        with psycopg.connect(cls._dsn(cls.owner)) as owner:
            for path in sorted((ROOT / "migrations" / "postgres").glob("*.sql")):
                try:
                    owner.execute(path.read_text(encoding="utf-8"))
                    owner.commit()
                except Exception:
                    owner.rollback()
                    if path.name not in TOLERATED_MIGRATIONS:
                        raise
                if owner.execute("SELECT to_regclass('public.tenants')").fetchone()[0] and \
                        owner.execute("SELECT 1 FROM tenants").fetchone() is None:
                    owner.execute("INSERT INTO tenants(tenant_id, name, status, created_at) "
                                  "VALUES (%s, 'Test', 'active', '2026-01-01')", (TENANT,))
                    owner.commit()
            owner.execute("INSERT INTO accounts(account_id, base_currency, first_seen_at, last_seen_at, source_json) "
                          "VALUES ('UDESK1', 'USD', '2026-01-01', '2026-01-01', '{}')")
            owner.execute(f"GRANT USAGE ON SCHEMA public TO {cls.web}")
            owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {cls.web}")
            owner.execute(f"GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {cls.web}")
            for layer in ("bronze", "silver", "gold"):
                if owner.execute("SELECT to_regnamespace(%s)", (layer,)).fetchone()[0]:
                    owner.execute(f"GRANT USAGE ON SCHEMA {layer} TO {cls.web}")
                    owner.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {layer} TO {cls.web}")
            owner.commit()
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        import psycopg

        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{cls.database}" WITH (FORCE)')
            for role in (cls.web, cls.owner):
                admin.execute(f"DROP ROLE IF EXISTS {role}")

    @classmethod
    def _dsn(cls, user: str | None = None) -> str:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        params = conninfo_to_dict(ADMIN_DSN)
        params["dbname"] = cls.database
        params["options"] = f"-c app.tenant_id={TENANT}"
        if user:
            params["user"] = user
            params["password"] = TEST_PASSWORD
        return make_conninfo(**params)

    @classmethod
    def _seed(cls):
        import psycopg

        snapshot = NOW - timedelta(seconds=10)
        with psycopg.connect(cls._dsn(), autocommit=True) as admin:
            admin.execute("SET search_path = bronze, public")
            admin.execute(
                "INSERT INTO live_account_values(tenant_id, account_id, observed_at, base_currency, net_liquidation, "
                "total_cash, daily_pnl, cash_by_currency) VALUES (%s, 'UDESK1', %s, 'USD', 100000, 5000, 120, "
                "'{\"USD\": 5000}')", (TENANT, snapshot))
            admin.execute(
                "INSERT INTO live_positions(tenant_id, account_id, observed_at, conid, symbol, sec_type, currency, "
                "quantity, market_price, market_value) VALUES (%s, 'UDESK1', %s, %s, 'MSFT', 'STK', 'USD', 50, 198, "
                "9900)", (TENANT, snapshot, MSFT))
            for conid, symbol, last, prior, benchmark in ((MSFT, "MSFT", 198, 200, False),
                                                          ("416904", "SPX", 5050, 5000, True)):
                admin.execute(
                    "INSERT INTO live_contracts(tenant_id, conid, symbol, sec_type, currency, is_benchmark, "
                    "refreshed_at) VALUES (%s, %s, %s, %s, 'USD', %s, %s)",
                    (TENANT, conid, symbol, "IND" if benchmark else "STK", benchmark, NOW))
                admin.execute(
                    "INSERT INTO live_quotes(tenant_id, conid, observed_at, symbol, currency, last_price, bid_price, "
                    "ask_price, prior_close, market_data_type) VALUES (%s, %s, %s, %s, 'USD', %s, %s, %s, %s, 1)",
                    (TENANT, conid, NOW - timedelta(seconds=30), symbol, last, last - 0.1, last + 0.1, prior))
            admin.execute(
                "INSERT INTO live_executions(tenant_id, exec_id, account_id, executed_at, conid, symbol, sec_type, "
                "currency, side, quantity, price, first_seen_at, updated_at) VALUES (%s, 'x1', 'UDESK1', %s, %s, "
                "'MSFT', 'STK', 'USD', 'BUY', 20, 199, %s, %s)",
                (TENANT, NOW - timedelta(hours=1), MSFT, NOW, NOW))
            admin.execute("SET search_path = public")
            # A benchmark whose id is the live index symbol, so its live quote is SPX's.
            admin.execute(
                "INSERT INTO benchmark_master(tenant_id, benchmark_id, symbol, name, provider, currency, "
                "return_convention, created_at, updated_at) VALUES (%s, 'SPX', '^SPXDESKTEST', 'Desk test index', "
                "'test', 'USD', 'price', '2026-01-01', '2026-01-01')", (TENANT,))
            admin.execute(
                "INSERT INTO portfolio_benchmark_mapping(tenant_id, subject_id, benchmark_id, mapping_type, "
                "effective_from, reporting_currency, created_at) VALUES (%s, 'UDESK1', 'SPX', 'primary', "
                "'2026-01-01', 'USD', '2026-01-01')", (TENANT,))

    @contextmanager
    def _website(self):
        """A connection like web._database(): the website role, read only, dict rows, the tenant set."""

        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn(self.web), row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
            yield connection

    def _read(self, connection):
        return web_desk.read_inputs(connection, TENANT, "UDESK1", reporting_currency="USD",
                                    comparison_version=lambda subject, currency: "v1", now=NOW)

    def _payload(self, inputs):
        limits, settings, source = web_desk.desk_limits({})
        return web_desk.desk_payload(inputs, limits=limits, settings=settings, now=NOW, limits_source=source)

    def test_views_missing_then_present_without_a_grant_then_granted(self):
        import psycopg

        # 1. Before desk_live_views: the Desk reads, says what is missing, and the transaction stays usable.
        with self._website() as connection:
            inputs = self._read(connection)
            self.assertEqual(connection.execute("SELECT 1 AS one").fetchone()["one"], 1)
        codes = [m["code"] for m in inputs["missing"]]
        self.assertIn("executions_view_missing", codes)
        self.assertIn("benchmark_view_missing", codes)
        self.assertIsNone(inputs["executions"])
        payload = self._payload(inputs)
        self.assertTrue(payload["available"])
        self.assertAlmostEqual(payload["results"]["desk_snapshot"]["pnl"]["total"]["pnl"], -100.0)

        # 2. desk_live_views has run but the website role has no grant yet: read in a savepoint, reported.
        with psycopg.connect(self._dsn(self.owner)) as owner:
            if DESK_VIEWS_DRAFT.exists():
                owner.execute(DESK_VIEWS_DRAFT.read_text(encoding="utf-8"))
            else:
                owner.execute(FALLBACK_VIEWS)
            owner.execute(f"REVOKE ALL ON silver.subject_live_executions, silver.benchmark_live_quotes, "
                          f"public.subject_live_executions, public.benchmark_live_quotes FROM {self.web}")
            owner.commit()
        with self._website() as connection, self.assertLogs("uvicorn.error.desk", "WARNING"):
            inputs = self._read(connection)
            self.assertEqual(connection.execute("SELECT 1 AS one").fetchone()["one"], 1)
        codes = [m["code"] for m in inputs["missing"]]
        self.assertIn("executions_view_unreadable", codes)
        self.assertIn("benchmark_view_unreadable", codes)
        self.assertEqual(len(inputs["positions"]), 1)

        # 3. Granted, as bootstrap_postgres.py grants every silver view: fills and the market factor arrive.
        with psycopg.connect(self._dsn(self.owner)) as owner:
            owner.execute(f"GRANT SELECT ON silver.subject_live_executions, silver.benchmark_live_quotes, "
                          f"public.subject_live_executions, public.benchmark_live_quotes TO {self.web}")
            owner.commit()
        with self._website() as connection:
            inputs = self._read(connection)
        self.assertEqual([m["code"] for m in inputs["missing"]], [])
        self.assertEqual([fill["exec_id"] for fill in inputs["executions"]], ["x1"])
        self.assertEqual(inputs["benchmark_quote"]["symbol"], "SPX")
        payload = self._payload(inputs)
        total = payload["results"]["desk_snapshot"]["pnl"]["total"]
        self.assertAlmostEqual(total["trading_pnl"], 20.0)
        self.assertAlmostEqual(total["pnl"], -80.0)
        self.assertAlmostEqual(payload["inputs"]["benchmark"]["return_today"], 0.01)


if __name__ == "__main__":
    unittest.main()
