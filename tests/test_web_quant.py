"""Quant analytics pages (web_quant.py): settings, gold reads, request-time models, cards and guide.

Synthetic data only (account U9100001, holdings S1000-S1005). The browser renderers run in
Node against payloads built by the real engines; they are skipped where Node is not installed.
The PostgreSQL check of the gold read needs AGENT_TEST_PG_ADMIN_DSN and is skipped otherwise.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ibkr_etl import metrics_guide, metrics_guide_quant as quant_guide, modelling, risk_forecasting, web_quant
from ibkr_etl.metrics_guide import render_metrics_guide
from ibkr_etl.quant import (attribution, crowding, decision_attribution, dynamic_correlation, execution_analysis,
                            holdings_risk, skill_statistics, stress_engine, tail_risk)
from ibkr_etl.web_analytics_ui import ANALYTICS_SCRIPT
from ibkr_etl.web_modelling import install as install_modelling
from ibkr_etl.web_ui import _NUMBER_HELPERS

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "U9100001"
TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = {"email": "owner@example.com", "object_id": "owner-1", "role": "owner"}
VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer"}
ALL_ON = "performance=on,risk=on,modelling=on"
ADMIN_DSN = os.environ.get("AGENT_TEST_PG_ADMIN_DSN", "")
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node",) if Path(p).exists()), None)


def _weekdays(start: date, count: int) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _market() -> dict:
    """640 sessions of a market with two stressed spells, six holdings and a NAV that follows them."""

    generator = random.Random(42)
    days = _weekdays(date(2023, 1, 2), 640)
    market = [generator.gauss(-0.0015, 0.022) if 150 <= i < 230 or 430 <= i < 480 else generator.gauss(0.0006, 0.006)
              for i in range(len(days))]
    conids = [str(1000 + i) for i in range(6)]
    betas = [0.6, 0.8, 1.0, 1.1, 1.3, 0.9]
    returns = {c: [b * m + generator.gauss(0.0001, 0.008) for m in market] for c, b in zip(conids, betas)}
    prices = {}
    for conid in conids:
        level, series = 100.0, {}
        for day, move in zip(days, returns[conid]):
            level *= 1.0 + move
            series[day] = level
        prices[conid] = series
    nav, level = [], 1_000_000.0
    for i, day in enumerate(days):
        level *= 1.0 + sum(0.15 * returns[c][i] for c in conids)
        nav.append((day, f"{level:.2f}"))
    factors = {day: {"Mkt-RF": m - 0.0001, "SMB": generator.gauss(0, 0.003), "HML": generator.gauss(0, 0.004),
                     "RMW": generator.gauss(0, 0.003), "CMA": generator.gauss(0, 0.003), "RF": 0.0001}
               for day, m in zip(days, market)}
    return {"days": days, "market": market, "conids": conids, "returns": returns, "prices": prices, "nav": nav,
            "factors": factors}


MARKET = _market()


def _payloads() -> dict:
    """Every gold section's payload, built by calling the real engines on the synthetic market."""

    days, prices, returns = MARKET["days"], MARKET["prices"], MARKET["returns"]
    symbols = {c: f"S{c}" for c in MARKET["conids"]}
    weights = {symbols[c]: 0.15 for c in MARKET["conids"]}
    closes = {symbols[c]: prices[c] for c in MARKET["conids"]}
    navs = [float(v) for _, v in MARKET["nav"]]
    daily = [(day, navs[i] / navs[i - 1] - 1.0) for i, day in enumerate(days) if i]
    portfolio = [r for _, r in daily]
    bench = list(zip(days[1:], MARKET["market"][1:]))
    columns = {symbols[c]: returns[c][-252:] for c in MARKET["conids"]}
    trades = [{"trade_date": days[i], "conid": s, "symbol": s, "side": side, "quantity": 100.0,
               "price": closes[s][days[i]] * 1.001, "commission": -2.0}
              for i, s, side in ((400, "S1000", "BUY"), (450, "S1001", "SELL"), (500, "S1002", "BUY"),
                                 (520, "S1000", "SELL"), (560, "S1003", "BUY"))]
    value_added = decision_attribution.trade_value_added(trades, closes)
    positions = {days[i]: {s: 100.0 * (1 + 0.1 * j) for j, s in enumerate(closes)} for i in range(300, 600, 5)}
    forward = decision_attribution.holding_forward_returns(positions, closes, horizon=21)
    start = days[-120]
    fit = risk_forecasting.choose_regimes(portfolio[-risk_forecasting.REGIME_HISTORY:])

    def currency(scale: float) -> dict:
        return attribution.currency_attribution(
            {"US": 0.6, "HK": 0.4}, {"US": 0.01 * scale, "HK": 0.02 * scale}, {"US": 0.7, "HK": 0.3},
            {"US": 0.008 * scale, "HK": 0.01 * scale}, {"US": "USD", "HK": "HKD"},
            {"USD": 0.0, "HKD": 0.001 * scale}, base_currency="USD")

    covariance = holdings_risk.ledoit_wolf_covariance(columns)
    # As quant_snapshots.build_execution stores them: one entry per order wrapping the engine's
    # dict, totals by month, and the orders it could not measure.
    orders, months = [], {}
    for trade in trades:
        index = days.index(trade["trade_date"])
        result = execution_analysis.implementation_shortfall(
            {"side": trade["side"], "quantity": trade["quantity"]},
            [{"quantity": trade["quantity"], "price": trade["price"], "commission": 2.0}],
            decision_price=closes[trade["symbol"]][days[index - 1]], arrival_price=closes[trade["symbol"]][days[index - 1]],
            final_price=closes[trade["symbol"]][trade["trade_date"]])
        orders.append({"trade_date": trade["trade_date"], "conid": trade["conid"], "symbol": trade["symbol"],
                       "side": trade["side"], "fills": 1, "decision_date": days[index - 1],
                       "implementation_shortfall": result})
        month = months.setdefault(trade["trade_date"][:7], {"orders": 0, "paper_value": 0.0, "delay": 0.0, "trading": 0.0,
                                                           "commission": 0.0, "opportunity": 0.0, "total": 0.0})
        month["orders"] += 1
        month["paper_value"] += result["paper_value"]
        for part in ("delay", "trading", "commission", "opportunity"):
            month[part] += result["components"][part]["amount"]
        month["total"] += result["total"]["amount"]
    by_month = [{"month": key, **value, "total_bps": value["total"] / value["paper_value"] * 10_000}
                for key, value in sorted(months.items())]
    evt = tail_risk.evt_var_es(portfolio, levels=(0.99, 0.995))
    basis = "conditional" if evt.get("conditional") else "unconditional"
    labels = risk_forecasting.REGIME_LABELS[len(fit["means"])]
    results = {
        "skill": {"skill_summary": skill_statistics.skill_summary(daily, bench, samples=200)},
        "decisions": {
            "trade_value_added": value_added,
            "skill_profile": decision_attribution.skill_profile(
                decision_attribution.results_at_horizon(value_added, 21), forward),
            "timing_cost": decision_attribution.timing_cost(trades, closes),
            "counterfactual_portfolios": decision_attribution.counterfactual_portfolios(
                start, {s: 1000 for s in closes}, closes, {d: float(v) for d, v in MARKET["nav"] if d >= start},
                benchmark={d: 100.0 * (1 + 0.0002 * i) for i, d in enumerate(days)}),
        },
        "currency": {"mtd": currency(0.2), "ytd": currency(1.0)},
        "tail": {"evt_var_es": evt,
                 "tail_backtest_report": tail_risk.tail_backtest_report(daily, levels=(0.975, 0.99), simulations=200),
                 "forecasts": {"as_of": days[-1], "horizon_days": 1, "basis": basis,
                               "next_day_volatility": evt[basis].get("next_day_volatility"),
                               "levels": {key: {"level": row.get("level"), "var": row.get("var"),
                                                "expected_shortfall": row.get("expected_shortfall")}
                                          for key, row in evt[basis]["levels"].items()}}},
        "regime": {"regime_model": {"available": True, "states": len(labels), "labels": list(labels),
                                    **{key: fit[key] for key in ("means", "sds", "transition", "shares")},
                                    "today": dict(zip(labels, fit["filtered"][-1]))},
                   "regime_risk": stress_engine.regime_risk(
                       portfolio[-risk_forecasting.REGIME_HISTORY:], fit["filtered"], labels=labels)},
        "holdings_risk": {"ledoit_wolf_covariance": covariance, "ex_ante_risk": holdings_risk.ex_ante_risk(weights, covariance),
                          "statistical_factors": holdings_risk.statistical_factors(columns, 3)},
        "correlation": {"dcc_correlation": dynamic_correlation.dcc_correlation(
            {s: columns[s] for s in list(columns)[:5]}, weights={s: weights[s] for s in list(columns)[:5]})},
        "concentration": {"concentration": crowding.concentration(weights),
                          "concentration_by_sector": crowding.concentration({"Energy": 0.4, "Technology": 0.5}),
                          "correlation_clusters": crowding.correlation_clusters(columns, threshold=0.7, weights=weights)},
        "execution": {"implementation_shortfall": orders, "by_month": by_month,
                      "skipped": [{"trade_date": days[2], "conid": "S1004", "symbol": "S1004", "side": "BUY",
                                   "reason": "no close before the trade date"}]},
    }
    summaries = {"execution": {"orders": len(orders), "orders_skipped": 1, "orders_left_out": 0,
                               "window": [orders[0]["trade_date"], orders[-1]["trade_date"]], "currency": "USD",
                               "prices": "decision and arrival: the previous close; final: the close on the trade date"}}
    payloads = {}
    for section, result in results.items():
        model_id = web_quant.SECTIONS[section][1]
        payloads[section] = {"section": section, "model_id": model_id, "model_version": next(iter(result.values())).get(
            "model_version") if isinstance(next(iter(result.values())), dict) else None,
            "as_of": days[-1], "available": True, "reason": None,
            "inputs": summaries.get(section) or {"observations": len(daily), "window": [days[1], days[-1]], "holdings": 6},
            "results": result}
    return payloads


_PAYLOADS: dict | None = None


def payloads() -> dict:
    global _PAYLOADS
    if _PAYLOADS is None:
        _PAYLOADS = _payloads()
    return _PAYLOADS


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    """The website database as the Modelling tab and the gold read see it."""

    def __init__(self):
        self.snapshots: dict[str, list[dict]] = {}
        self.missing_table = False
        self.statements: list[tuple[str, tuple]] = []
        self.savepoints = 0

    @contextlib.contextmanager
    def transaction(self):
        self.savepoints += 1
        yield self

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if "quant_analytics_snapshots" in sql:
            if self.missing_table:
                raise psycopg.errors.UndefinedTable('relation "quant_analytics_snapshots" does not exist')
            _tenant, subject, section, version, as_of, _ = params
            rows = [row for row in self.snapshots.get(section, ())
                    if row["subject_id"] == subject and row["analytics_version"] == version
                    and row["quality_status"] in ("pass", "warn") and (as_of is None or row["as_of_date"] <= as_of)]
            return _Result(sorted(rows, key=lambda row: row["as_of_date"], reverse=True)[:1])
        if "risk_analytics_snapshots" in sql:
            return _Result([{"payload_json": {"metrics": {"risk_free_annual_rate": 0.04}}}])
        if "subject_position_series" in sql:
            return _Result([{"snapshot_date": day, "conid": conid, "mark_price_text": f"{value:.6f}"}
                            for conid, series in MARKET["prices"].items() for day, value in series.items()])
        raise AssertionError(sql)

    def quant_reads(self) -> int:
        return sum("quant_analytics_snapshots" in sql for sql, _ in self.statements)


def _row(section: str, payload: dict, as_of: str, quality: str = "pass", version: str = "quant-analytics-v1") -> dict:
    return {"subject_id": ACCOUNT, "as_of_date": as_of, "section": section, "analytics_version": version,
            "model_id": payload["model_id"], "model_version": payload.get("model_version"),
            "payload_json": json.dumps(payload), "quality_status": quality, "generated_at": f"{as_of}T09:31:00+00:00"}


class _App:
    """web_modelling and web_quant installed on one app over the fake database."""

    def __init__(self, subject_type: str = "physical_account"):
        self.user = dict(OWNER)
        self.subject_type = subject_type
        self.connection = _Connection()
        days = MARKET["days"]
        snapshot = days[-1]
        last = {conid: series[snapshot] for conid, series in MARKET["prices"].items()}
        self.holdings = [{"snapshot_date": snapshot, "conid": conid} for conid in MARKET["conids"]]
        enriched = [{"conid": conid, "symbol": f"S{conid}", "description": f"Holding {conid}", "weight": 0.15,
                     "reporting_market_value_text": "150000.00", "quantity_text": f"{150000.0 / last[conid]:.6f}"}
                    for conid in MARKET["conids"]]
        app = FastAPI()
        reads = install_modelling(
            app,
            authorized_user=lambda: self.user,
            access_context=lambda account, user: {"reporting_currency": "USD", "subject_type": self.subject_type},
            parse_as_of=lambda value: value,
            read_database=lambda: contextlib.nullcontext(self.connection),
            nav_values=lambda connection, account, access, through: MARKET["nav"],
            external_flows=lambda connection, account, access: {},
            latest_holdings=lambda connection, account, as_of: self.holdings,
            enrich_holdings=lambda connection, account, rows, **kwargs: {"holdings": [dict(row) for row in enriched]},
            comparison_version=lambda account, currency: "v1",
            tenant_id=TENANT,
            risk_analytics_version="r1",
            load_factors=lambda region: {"region_label": "Developed markets", "source_url": "https://example.test",
                                         "factors": MARKET["factors"], "latest_date": max(MARKET["factors"]),
                                         "momentum_note": "Momentum is left out in this test.",
                                         "source": "kenneth_french", "labels": None, "source_note": ""},
        )
        web_quant.install(app, authorized_user=lambda: self.user,
                          access_context=lambda account, user: {"reporting_currency": "USD",
                                                                "subject_type": self.subject_type},
                          parse_as_of=lambda value: value,
                          read_database=lambda: contextlib.nullcontext(self.connection),
                          modelling=reads, tenant_id=TENANT)
        self.client = TestClient(app)

    def get(self, path: str, **params):
        return self.client.get(f"/api/portfolio/{ACCOUNT}/{path}", params=params)


QUANT_ROUTES = [f"quant/{section}" for section in web_quant.SECTIONS] + [
    "modelling/optimisers", "modelling/rebalancing", "modelling/goals", "modelling/scenarios"]


def _setting(value: str | None):
    # QUANT_ANALYTICS_PHASE3 stays unset too: these are the v1 pages (phase 3 is _settings, below).
    environ = {k: v for k, v in os.environ.items() if k not in (web_quant.PAGES_SETTING, "QUANT_ANALYTICS_PHASE3")}
    if value is not None:
        environ[web_quant.PAGES_SETTING] = value
    return mock.patch.dict(os.environ, environ, clear=True)


class SettingsTest(unittest.TestCase):
    def test_settings_parse_each_page_and_ignore_anything_unknown(self):
        self.assertEqual(web_quant.page_modes({}), {"performance": "off", "risk": "off", "modelling": "off"})
        self.assertEqual(web_quant.page_modes({web_quant.PAGES_SETTING: "performance=shadow,risk=on,modelling=off"}),
                         {"performance": "shadow", "risk": "on", "modelling": "off"})
        self.assertEqual(web_quant.page_modes({web_quant.PAGES_SETTING: " Risk = ON , desk=on, modelling=maybe, performance"}),
                         {"performance": "off", "risk": "on", "modelling": "off"})
        self.assertEqual(web_quant.page_modes({web_quant.PAGES_SETTING: "risk=on,risk=off"})["risk"], "off")

    def test_shadow_is_owners_only_and_on_is_everyone_with_access(self):
        environ = {web_quant.PAGES_SETTING: "performance=shadow,risk=on"}
        self.assertTrue(web_quant.page_allowed("performance", OWNER, environ))
        self.assertFalse(web_quant.page_allowed("performance", VIEWER, environ))
        self.assertFalse(web_quant.page_allowed("performance", {"email": "no-role@example.com"}, environ))
        self.assertTrue(web_quant.page_allowed("risk", VIEWER, environ))
        self.assertFalse(web_quant.page_allowed("modelling", OWNER, environ))
        self.assertEqual(web_quant.allowed_pages(VIEWER, environ), ("risk",))
        self.assertEqual(web_quant.allowed_pages(OWNER, environ), ("performance", "risk"))

    def test_page_pieces_are_empty_when_off(self):
        with _setting(None):
            for page in web_quant.PAGES:
                self.assertEqual(web_quant.subpages(page, OWNER), ())
                self.assertEqual(web_quant.page_cards(page, OWNER), "")
            self.assertEqual(web_quant.page_css(OWNER) + web_quant.page_script(OWNER) + web_quant.render_hook(OWNER), "")
            self.assertEqual(web_quant.guide_additions(OWNER), ())
        with _setting("performance=shadow"):
            self.assertEqual(web_quant.subpages("performance", VIEWER), ())
            self.assertEqual(len(web_quant.subpages("performance", OWNER)), 3)
            self.assertIn('id="quant-skill"', web_quant.page_cards("performance", OWNER))
            self.assertEqual(web_quant.page_cards("risk", OWNER), "")

    def test_subpage_slugs_and_card_ids_are_new(self):
        from ibkr_etl import web

        for page, parts in web_quant.QUANT_SUBPAGES.items():
            existing = web.ACCOUNT_SUBPAGES[page]
            self.assertFalse({slug for slug, *_ in parts} & {slug for slug, *_ in existing}, page)
            for _slug, _label, anchor in parts:
                self.assertTrue(anchor.startswith("quant-"))
                self.assertEqual(web_quant.QUANT_CARDS[page].count(f'id="{anchor}"'), 1)
                self.assertIn(f'id="{anchor}" hidden', web_quant.QUANT_CARDS[page])


class OffByDefaultTest(unittest.TestCase):
    def test_every_route_is_404_and_reads_nothing_while_unset(self):
        app = _App()
        app.connection.snapshots = {s: [_row(s, {"model_id": "x"}, "2025-06-30")] for s in web_quant.SECTIONS}
        with _setting(None):
            for path in QUANT_ROUTES:
                for user in (OWNER, VIEWER):
                    app.user = dict(user)
                    with self.subTest(path=path, role=user["role"]):
                        response = app.get(path)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.json(), {"detail": "Not Found"})
        self.assertEqual(app.connection.statements, [])

    def test_a_page_switched_off_stays_404_beside_one_switched_on(self):
        app = _App()
        with _setting("performance=on,risk=off"):
            self.assertEqual(app.get("quant/tail").status_code, 404)
            self.assertEqual(app.get("quant/execution").status_code, 404)
            self.assertEqual(app.get("modelling/goals").status_code, 404)
            self.assertEqual(app.get("quant/nonsense").status_code, 404)
        self.assertEqual(app.connection.quant_reads(), 0)


class GoldReadTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.payload = payloads()["skill"]
        self.app.connection.snapshots["skill"] = [
            _row("skill", self.payload, "2025-06-27"),
            _row("skill", {**self.payload, "as_of": "2025-06-30"}, "2025-06-30", quality="warn"),
            _row("skill", self.payload, "2025-07-01", quality="fail"),
            _row("skill", self.payload, "2025-07-02", version="quant-analytics-v0"),
        ]

    def test_shadow_shows_the_section_to_owners_only(self):
        with _setting("performance=shadow"):
            owner = self.app.get("quant/skill", as_of="2025-07-03")
            self.app.user = dict(VIEWER)
            viewer = self.app.get("quant/skill", as_of="2025-07-03")
        self.assertEqual(owner.status_code, 200, owner.text)
        self.assertEqual(viewer.status_code, 404)
        body = owner.json()
        # The newest row on or before the date whose quality is not fail, in this version.
        self.assertEqual(body["as_of_date"], "2025-06-30")
        self.assertEqual(body["quality_status"], "warn")
        self.assertEqual(body["page_mode"], "shadow")
        self.assertEqual(body["model_id"], "skill-statistics")
        self.assertEqual(body["model_status"], "draft")
        self.assertTrue(body["model_limits"])
        self.assertEqual(body["results"]["skill_summary"]["verdict"], self.payload["results"]["skill_summary"]["verdict"])
        self.assertEqual(body["generated_at"], "2025-06-30T09:31:00+00:00")
        sql, params = next((s, p) for s, p in self.app.connection.statements if "quant_analytics_snapshots" in s)
        self.assertIn("quality_status IN ('pass', 'warn')", sql)
        self.assertIn("as_of_date <= %s", sql)
        self.assertIn("ORDER BY as_of_date DESC", sql)
        self.assertEqual(params, (TENANT, ACCOUNT, "skill", "quant-analytics-v1", "2025-07-03", "2025-07-03"))

    def test_on_shows_everyone_and_an_earlier_date_reads_an_earlier_row(self):
        self.app.user = dict(VIEWER)
        with _setting("performance=on"):
            body = self.app.get("quant/skill", as_of="2025-06-28").json()
            too_early = self.app.get("quant/skill", as_of="2025-01-01")
        self.assertEqual(body["as_of_date"], "2025-06-27")
        self.assertEqual(too_early.status_code, 404)
        self.assertIn("No skill and luck figures", too_early.json()["detail"])

    def test_a_missing_relation_is_404_inside_a_savepoint(self):
        self.app.connection.missing_table = True
        with _setting("performance=on"):
            response = self.app.get("quant/skill")
        self.assertEqual(response.status_code, 404)
        self.assertIn("have been built", response.json()["detail"])
        self.assertEqual(self.app.connection.savepoints, 1)

    def test_composite_and_other_access_rules_are_the_portfolio_apis(self):
        def refuse(account, user):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Portfolio not available")

        app = FastAPI()
        connection = _Connection()
        web_quant.install(app, authorized_user=lambda: OWNER, access_context=refuse, parse_as_of=lambda v: v,
                          read_database=lambda: contextlib.nullcontext(connection), modelling=None, tenant_id=TENANT)
        with _setting("performance=on"):
            response = TestClient(app).get(f"/api/portfolio/{ACCOUNT}/quant/skill")
        self.assertEqual(response.json(), {"detail": "Portfolio not available"})
        self.assertEqual(connection.statements, [])

    def test_bad_as_of_is_rejected_by_the_portal_parser(self):
        def parse(value):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="as_of must be YYYY-MM-DD")

        app = FastAPI()
        web_quant.install(app, authorized_user=lambda: OWNER, access_context=lambda a, u: {}, parse_as_of=parse,
                          read_database=lambda: contextlib.nullcontext(_Connection()), modelling=None, tenant_id=TENANT)
        with _setting("performance=on"):
            self.assertEqual(TestClient(app).get(f"/api/portfolio/{ACCOUNT}/quant/skill?as_of=x").status_code, 400)


@unittest.skipUnless(ADMIN_DSN, "AGENT_TEST_PG_ADMIN_DSN is not set")
class GoldReadPostgresTest(unittest.TestCase):
    """The gold query and the held-migration case against a real PostgreSQL."""

    def setUp(self):
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        self.database = f"quant_web_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{self.database}"')
        params = conninfo_to_dict(ADMIN_DSN)
        params["dbname"] = self.database
        self.dsn = make_conninfo(**params)

    def tearDown(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{self.database}" WITH (FORCE)')

    def _website(self):
        from psycopg.rows import dict_row

        connection = psycopg.connect(self.dsn, row_factory=dict_row)
        connection.execute("SET TRANSACTION READ ONLY")
        return connection

    def test_missing_table_rolls_back_to_a_usable_connection_then_rows_are_filtered(self):
        with self._website() as connection:
            self.assertIsNone(web_quant.read_snapshot(connection, tenant_id=TENANT, subject_id=ACCOUNT,
                                                      section="skill", as_of=None))
            self.assertEqual(connection.execute("SELECT 1 AS one").fetchone()["one"], 1)
        with psycopg.connect(self.dsn, autocommit=True) as owner:
            owner.execute("""CREATE TABLE quant_analytics_snapshots (tenant_id uuid, subject_id text, as_of_date text,
                section text, analytics_version text, model_id text, model_version text, payload_json jsonb,
                quality_status text, generated_at timestamptz)""")
            for as_of, quality, version in (("2025-06-27", "pass", "quant-analytics-v1"),
                                            ("2025-06-30", "warn", "quant-analytics-v1"),
                                            ("2025-07-01", "fail", "quant-analytics-v1"),
                                            ("2025-07-02", "pass", "quant-analytics-v0")):
                owner.execute("INSERT INTO quant_analytics_snapshots VALUES (%s, %s, %s, 'skill', %s, 'skill-statistics', "
                              "'quant-skill-statistics-v1', %s, %s, '2025-07-03T09:31:00Z')",
                              (TENANT, ACCOUNT, as_of, version, json.dumps({"as_of": as_of, "results": {}}), quality))
        with self._website() as connection:
            row = web_quant.read_snapshot(connection, tenant_id=TENANT, subject_id=ACCOUNT, section="skill",
                                          as_of="2025-07-05")
            earlier = web_quant.read_snapshot(connection, tenant_id=TENANT, subject_id=ACCOUNT, section="skill",
                                              as_of="2025-06-29")
            other = web_quant.read_snapshot(connection, tenant_id=TENANT, subject_id="U9100002", section="skill",
                                            as_of=None)
        self.assertEqual((row["as_of_date"], row["quality_status"]), ("2025-06-30", "warn"))
        self.assertEqual(row["payload_json"]["as_of"], "2025-06-30")
        self.assertEqual(earlier["as_of_date"], "2025-06-27")
        self.assertIsNone(other)
        body = web_quant.section_response(ACCOUNT, "skill", row, "on")
        self.assertTrue(body["generated_at"].startswith("2025-07-03T09:31:00"))


def _node(code: str) -> dict:
    result = subprocess.run([NODE, "-"], input=code, text=True, encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr[-4000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def _page_helpers() -> str:
    """The account page's own formatting helpers, taken from the page code, and a small DOM."""

    from ibkr_etl import web

    html = web._render_account_page(OWNER, account_id=web.PILOT_ACCOUNT_ID, section="performance")
    esc = re.search(r"const esc=v=>.*?;\n", html).group(0)
    pct = re.search(r"const pct=n=>\{.*?\};\n", html).group(0)
    analytics = "".join(re.search(rf"const {name}=.*?\n", ANALYTICS_SCRIPT).group(0)
                        for name in ("analyticsDate", "analyticsMonth", "ratio"))
    return (
        f"const account={json.dumps(ACCOUNT)},reportingCurrency='USD',activeSection='performance';\n"
        + esc + pct + _NUMBER_HELPERS + analytics
        + """
const cards={},requests=[],fields={};let responses={};
const card=id=>{const parts={'.quant-body':{innerHTML:''},'.quant-note':{textContent:''},'.quant-model':{textContent:''}};
  cards[id]={id,hidden:true,parts,querySelector:s=>parts[s]};return cards[id]};
const view={querySelector:s=>cards[s.replace('#','')]||null};
const document={querySelector:s=>s==='.page-view.active'?view:s in fields?{value:fields[s]}:null,querySelectorAll:()=>[],getElementById:id=>cards[id]||null};
globalThis.fetch=async url=>{const [path,query]=url.split('?');const key=path.split('/').slice(4).join('/');requests.push([key,query]);
  const [status,body]=responses[key]||[404,{detail:'Not Found'}];return {ok:status<400,status,json:async()=>body}};
const shown=id=>{const c=cards[id];return {hidden:c.hidden,body:c.parts['.quant-body'].innerHTML,note:c.parts['.quant-note'].textContent,model:c.parts['.quant-model'].textContent}};
"""
        + web_quant.QUANT_SCRIPT
    )


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%".replace("-", "−")


@unittest.skipUnless(NODE, "Node is not installed in this test environment")
class SectionRenderingTest(unittest.TestCase):
    """Each card renders the payload the real engines produce, through the real route."""

    @classmethod
    def setUpClass(cls):
        cls.app = _App()
        for section, payload in payloads().items():
            cls.app.connection.snapshots[section] = [_row(section, payload, MARKET["days"][-1])]
        with _setting(ALL_ON):
            cls.responses = {f"quant/{section}": [200, cls.app.get(f"quant/{section}").json()]
                             for section in web_quant.SECTIONS}
        code = _page_helpers() + f"responses={json.dumps(cls.responses)};\n" + """
(async()=>{const out={};for(const [section,id,render] of quantSections){card(id);await quantLoadSection(section,id,render,'2025-06-30');out[section]=shown(id)}
  card('quant-missing');responses['quant/skill']=[404,{detail:'No skill and luck figures have been built for this account on or before this date yet.'}];
  await quantLoadSection('skill','quant-missing',quantRenderSkill,'2020-01-01');out.missing=shown('quant-missing');
  out.requests=requests;console.log(JSON.stringify(out))})();
"""
        cls.out = _node(code)

    def assertClean(self, shown):
        self.assertFalse(shown["hidden"])
        for text in (shown["body"], shown["note"], shown["model"]):
            for bad in ("undefined", "NaN", "[object Object]", "null"):
                self.assertNotIn(bad, text)

    def test_every_section_is_shown_with_its_model_and_method(self):
        for section in web_quant.SECTIONS:
            with self.subTest(section=section):
                shown = self.out[section]
                self.assertClean(shown)
                self.assertIn('class="metric"', shown["body"])
                self.assertIn("Model:", shown["model"])
                self.assertIn(self.responses[f"quant/{section}"][1]["model_name"], shown["model"])
                self.assertIn("draft", shown["model"])
        self.assertEqual(sorted(key for key, _ in self.out["requests"])[:2], ["quant/concentration", "quant/correlation"])
        self.assertEqual(len(self.out["requests"]), len(web_quant.SECTIONS) + 1)
        self.assertTrue(all(query == "as_of=2025-06-30" for _, query in self.out["requests"][:-1]))
        self.assertEqual(self.out["requests"][-1], ["quant/skill", "as_of=2020-01-01"])

    def test_a_section_not_built_yet_shows_a_note_not_figures(self):
        shown = self.out["missing"]
        self.assertFalse(shown["hidden"])
        self.assertEqual(shown["body"], "")
        self.assertIn("Not built yet", shown["note"])

    def test_known_figures_appear_as_the_engines_computed_them(self):
        results = {s: self.responses[f"quant/{s}"][1]["results"] for s in web_quant.SECTIONS}
        skill = results["skill"]["skill_summary"]
        self.assertIn(skill["verdict"], self.out["skill"]["body"])
        self.assertIn(_percent(skill["probabilistic_sharpe_ratio"]), self.out["skill"]["body"])
        self.assertIn("Sharpe ratio range", self.out["skill"]["body"])
        self.assertNotIn("Deflated Sharpe ratio", self.out["skill"]["body"])  # one trial: same as PSR, not shown
        evt = results["tail"]["evt_var_es"]["unconditional"]["levels"]["99"]
        self.assertIn(_percent(evt["var"]), self.out["tail"]["body"])
        self.assertIn(_percent(evt["historical_expected_shortfall"]), self.out["tail"]["body"])
        self.assertIn("quant-zone-" + results["tail"]["tail_backtest_report"]["basel"]["zone"], self.out["tail"]["body"])
        holding = results["concentration"]["concentration"]
        self.assertAlmostEqual(holding["effective_holdings"], 1 / holding["herfindahl"])
        self.assertIn(f"{holding['effective_holdings']:.1f}", self.out["concentration"]["body"])
        self.assertIn("Effective number (by sector)", self.out["concentration"]["body"])
        ex_ante = results["holdings_risk"]["ex_ante_risk"]
        self.assertAlmostEqual(sum(h["contribution"] for h in ex_ante["holdings"]), ex_ante["volatility"])
        self.assertIn(_percent(ex_ante["volatility"]), self.out["holdings_risk"]["body"])
        ytd = results["currency"]["ytd"]["totals"]
        effect = ytd["currency_allocation"] + ytd["currency_compounding"]
        self.assertIn(("+" if effect > 0 else "") + _percent(effect), self.out["currency"]["body"])
        regimes = results["regime"]["regime_risk"]["regimes"]
        for regime in regimes:
            self.assertIn(regime["regime"], self.out["regime"]["body"])
        today = results["regime"]["regime_model"]["today"]
        self.assertIn(f'<div class="label">Most likely regime today</div><div class="value">{max(today, key=today.get)}</div>',
                      self.out["regime"]["body"])
        dcc = results["correlation"]["dcc_correlation"]["average_correlation"]
        self.assertIn(f"{dcc['latest']:.2f}", self.out["correlation"]["body"])
        self.assertIn(" and ", self.out["correlation"]["body"])  # the top pairs
        totals = results["decisions"]["trade_value_added"]["totals"]
        self.assertEqual([t["horizon"] for t in totals], [5, 21, 63])
        self.assertIn("Value added (63 sessions)", self.out["decisions"]["body"])
        self.assertIn("Quarterly rebalance", self.out["decisions"]["body"])
        orders = results["execution"]["implementation_shortfall"]
        shortfall = sum(x["implementation_shortfall"]["total"]["amount"] for x in orders)
        self.assertIn(f"{abs(shortfall):,.2f}", self.out["execution"]["body"])
        self.assertIn(f'<div class="label">Orders analysed</div><div class="value">{len(orders)}</div>',
                      self.out["execution"]["body"])
        self.assertIn("By month", self.out["execution"]["body"])
        for month in results["execution"]["by_month"]:
            self.assertIn(f"{month['total_bps']:.1f} bp".replace("-", "−"), self.out["execution"]["body"])
        self.assertIn("1 order could not be measured.", self.out["execution"]["note"])
        self.assertIn("Prices: decision and arrival: the previous close", self.out["execution"]["note"])

    def test_other_layouts_of_the_ambiguous_sections_still_render(self):
        concentration = payloads()["concentration"]["results"]
        execution = payloads()["execution"]["results"]
        currency = payloads()["currency"]["results"]
        flat_orders = [{**x["implementation_shortfall"], "trade_date": x["trade_date"]} for x in execution["implementation_shortfall"]]
        code = _page_helpers() + f"""
const flat={json.dumps({"concentration_by_holding": concentration["concentration"],
                        "correlation_clusters": concentration["correlation_clusters"]})};
const nestedConcentration={json.dumps({"concentration": {"by_holding": concentration["concentration"],
                                                         "by_sector": concentration["concentration_by_sector"]}})};
const nested={json.dumps({"implementation_shortfall": {"trades": flat_orders,
                                                       "by_month": [{"month": "2025-03", "trades": 2, "amount": 12.5, "bps": 3.1}]}})};
const loose={json.dumps({"currency_attribution": currency})};
const out={{flat:quantRenderConcentration(flat)[0],nestedConcentration:quantRenderConcentration(nestedConcentration)[0],
  nested:quantRenderExecution(nested)[0],loose:quantRenderCurrency(loose)[0],empty:quantRenderTail({{}})[0]}};console.log(JSON.stringify(out));
"""
        out = _node(code)
        self.assertIn("Herfindahl index (by holding)", out["flat"])
        self.assertIn(f"{concentration['concentration']['effective_holdings']:.1f}", out["nestedConcentration"])
        self.assertIn("Effective number (by sector)", out["nestedConcentration"])
        self.assertIn("Mar 2025", out["nested"])
        self.assertIn("3.1 bp", out["nested"])
        self.assertIn(f'<div class="value">{len(flat_orders)}</div>', out["nested"])
        self.assertIn("Currency effect (year to date)", out["loose"])
        self.assertNotIn("undefined", out["empty"])

    def test_a_call_the_build_could_not_make_is_explained(self):
        code = _page_helpers() + """
responses['quant/skill']=[200,{section:'skill',model_id:'skill-statistics',model_name:'Skill statistics',model_status:'draft',
  model_version:'quant-skill-statistics-v1',available:true,results:{skill_summary:{available:false,reason:'Too few daily returns: 40 of the 252 needed.'}}}];
(async()=>{card('quant-skill');await quantLoadSection('skill','quant-skill',quantRenderSkill,'2025-06-30');console.log(JSON.stringify(shown('quant-skill')))})();
"""
        shown = _node(code)
        self.assertFalse(shown["hidden"])
        self.assertIn("Not worked out: Too few daily returns: 40 of the 252 needed.", shown["note"])
        for bad in ("undefined", "NaN", "[object Object]"):
            self.assertNotIn(bad, shown["body"] + shown["note"])



@unittest.skipUnless(NODE, "Node is not installed in this test environment")
class GoldBuilderRenderingTest(unittest.TestCase):
    """The cards render what the overnight build (quant_snapshots) stores, not only the engines.

    The payloads come from quant_snapshots.BUILDERS on the build's own synthetic data, stored as
    store_section stores them (finite numbers only), then read back through the route.
    """

    @classmethod
    def setUpClass(cls):
        import test_quant_snapshots as build_data
        from ibkr_etl import quant_snapshots

        inputs = quant_snapshots.prepare_inputs(build_data.synthetic_data(days=560))
        cls.app = _App()
        for section in web_quant.SECTIONS:
            payload, _ = quant_snapshots.finite_copy(quant_snapshots.BUILDERS[section](inputs[section]))
            cls.app.connection.snapshots[section] = [_row(section, payload, build_data.AS_OF)]
        with _setting(ALL_ON):
            cls.responses = {f"quant/{section}": [200, cls.app.get(f"quant/{section}").json()]
                             for section in web_quant.SECTIONS}
        code = _page_helpers() + f"responses={json.dumps(cls.responses)};\n" + """
(async()=>{const out={};for(const [section,id,render] of quantSections){card(id);await quantLoadSection(section,id,render,'2026-10-16');out[section]=shown(id)}
  console.log(JSON.stringify(out))})();
"""
        cls.out = _node(code)

    def test_every_tile_has_a_figure(self):
        for section in web_quant.SECTIONS:
            with self.subTest(section=section):
                shown = self.out[section]
                self.assertFalse(shown["hidden"])
                self.assertNotIn("Not worked out", shown["note"])
                for text in (shown["body"], shown["note"], shown["model"]):
                    for bad in ("undefined", "NaN", "[object Object]", "null"):
                        self.assertNotIn(bad, text)
                tiles = re.findall(r'<div class="label">([^<]*)</div><div class="value">(.*?)</div></div>', shown["body"])
                self.assertTrue(tiles)
                empty = [label for label, value in tiles if re.sub(r"<[^>]+>", "", value).strip() in ("", "—")]
                self.assertEqual(empty, [], f"{section} tiles with no figure")

    def test_the_local_market_and_currency_effects_add_up_to_the_excess_return(self):
        ytd = self.responses["quant/currency"][1]["results"]["ytd"]
        totals = ytd["totals"]
        local = totals["allocation"] + totals["selection"] + totals["interaction"]
        currency = totals["currency_allocation"] + totals["currency_compounding"]
        self.assertAlmostEqual(local + currency, ytd["excess_return"], places=12)
        body = self.out["currency"]["body"]
        self.assertIn(("+" if local > 0 else "") + _percent(local), body)
        self.assertIn(("+" if ytd["excess_return"] > 0 else "") + _percent(ytd["excess_return"]), body)

class ModellingEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _App()

    def get(self, path, **params):
        with _setting("modelling=on"):
            return self.app.get(path, **params)

    def test_optimisers_on_the_optimisation_pages_inputs(self):
        response = self.get("modelling/optimisers")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        today = self.get("modelling/optimise").json()
        self.assertAlmostEqual(body["portfolios"]["current"]["volatility"], today["portfolios"]["current"]["volatility"])
        self.assertAlmostEqual(body["portfolios"]["current"]["expected_return"],
                               today["portfolios"]["current"]["expected_return"])
        self.assertEqual(body["observations"], today["observations"])
        for name in web_quant.OPTIMISER_LABELS:
            with self.subTest(name=name):
                self.assertTrue(body["portfolios"][name]["available"], body["portfolios"][name])
                weights = [h[f"{name}_weight"] for h in body["holdings"]]
                self.assertAlmostEqual(sum(weights), 1.0, places=6)
                self.assertGreaterEqual(min(weights), -1e-9)
                if name != "hierarchical_risk_parity":
                    self.assertLessEqual(max(weights), 0.25 + 1e-6)
                self.assertGreater(body["portfolios"][name]["tail_loss_95"], 0)
        # With no views Black-Litterman keeps the market it starts from: today's sleeve (1/6 each).
        for holding in body["holdings"]:
            self.assertAlmostEqual(holding["black_litterman_weight"], 1 / 6, places=3)
        self.assertAlmostEqual(body["kappa"], math.sqrt(12.591587), places=4)  # χ²(6) at 95%
        self.assertEqual(body["disclaimer"], modelling.DISCLAIMER)
        self.assertEqual(body["model_status"], "draft")
        self.assertTrue(body["risk_free_available"])

    def test_views_costs_and_confidence_move_the_weights_the_right_way(self):
        plain = self.get("modelling/optimisers").json()
        viewed = self.get("modelling/optimisers", views="S1000:15:80, S1001>S1002:5").json()
        index = [h["symbol"] for h in viewed["holdings"]].index("S1000")
        self.assertGreater(viewed["holdings"][index]["black_litterman_weight"],
                           plain["holdings"][index]["black_litterman_weight"])
        self.assertEqual(viewed["views"][1]["against"], "S1002")
        cheap = self.get("modelling/optimisers", cost_bps="0").json()["portfolios"]["mean_variance_costs"]
        dear = self.get("modelling/optimisers", cost_bps="200").json()["portfolios"]["mean_variance_costs"]
        self.assertLessEqual(dear["turnover"], cheap["turnover"] + 1e-9)
        sure = self.get("modelling/optimisers", confidence="99").json()
        self.assertGreater(sure["kappa"], plain["kappa"])
        capped = self.get("modelling/optimisers", max_weight="18", turnover_limit="10").json()
        self.assertLessEqual(max(h["mean_variance_costs_weight"] for h in capped["holdings"]), 0.18 + 1e-6)
        self.assertLessEqual(capped["portfolios"]["mean_variance_costs"]["turnover"], 0.10 + 1e-6)

    def test_chi_square_quantile_known_answers(self):
        for probability, dof, expected in ((0.95, 1, 3.841459), (0.95, 2, 5.991465), (0.99, 10, 23.209251)):
            self.assertAlmostEqual(web_quant.chi_square_quantile(probability, dof), expected, places=4)

    def test_tail_loss_known_answer(self):
        columns = [[-0.05, 0.01, 0.02, -0.01] * 5, [0.0] * 20]
        # The worst 5% of 20 days is one day: the -5% day, at weight 0.5.
        self.assertAlmostEqual(web_quant.tail_loss([0.5, 0.5], columns), 0.025)

    def test_rebalancing_toward_an_optimiser_and_toward_target_weights(self):
        response = self.get("modelling/rebalancing", optimiser="minimum_variance")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["target_source"], "Minimum variance")
        self.assertAlmostEqual(sum(h["target_weight"] for h in body["holdings"]), 0.9, places=6)
        self.assertTrue(all(h["symbol"].startswith("S1") for h in body["holdings"]))
        self.assertGreaterEqual(body["cost_saved"], -1e-9)
        advanced = self.get("modelling/rebalancing", optimiser="hierarchical_risk_parity").json()
        self.assertEqual(advanced["target_source"], "Hierarchical risk parity")
        planned = self.get("modelling/rebalancing", target="S1000=0.3,S1001=0.05", cost_bps="10").json()
        rows = {h["symbol"]: h for h in planned["holdings"]}
        self.assertEqual(rows["S1000"]["action"], "buy to band edge")
        self.assertAlmostEqual(rows["S1000"]["weight_after"], rows["S1000"]["lower"])
        self.assertEqual(rows["S1001"]["action"], "sell to band edge")
        self.assertEqual(rows["S1002"]["target_weight"], 0.15)  # not named: keeps its weight
        trade = next(t for t in planned["trades"] if t["symbol"] == "S1000")
        nav = 150000.0 / 0.15
        self.assertAlmostEqual(trade["value"], (rows["S1000"]["lower"] - 0.15) * nav, places=4)
        self.assertAlmostEqual(trade["cost"], abs(trade["value"]) * 0.001, places=6)
        self.assertEqual(planned["disclaimer"], modelling.DISCLAIMER)

    def test_goals_with_history_and_regimes(self):
        response = self.get("modelling/goals", target="2000000", years="10", probability="80")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["target_source"], "yours")
        for name in ("history", "regimes"):
            with self.subTest(model=name):
                model = body["models"][name]
                self.assertTrue(model["available"], model)
                self.assertGreaterEqual(model["projection"]["probability_target"], 0.0)
                self.assertLessEqual(model["projection"]["probability_target"], 1.0)
                self.assertEqual(model["projection"]["horizon_months"], 120)
        needed = body["models"]["history"]["required"]
        if not needed["already_on_track"]:
            # Saving the amount it asks for reaches the target on at least 80% of the same paths.
            again = self.get("modelling/goals", target="2000000", years="10", probability="80",
                             monthly_contribution=str(math.ceil(needed["monthly_contribution"] * 100) / 100)).json()
            self.assertGreaterEqual(again["models"]["history"]["projection"]["probability_target"], 0.8 - 1e-9)
        default = self.get("modelling/goals").json()
        self.assertEqual(default["target_source"], "default")
        self.assertAlmostEqual(default["target"], default["start_value"] * 1.5)
        self.assertEqual(default["disclaimer"], modelling.DISCLAIMER)

    def test_scenarios_on_todays_factor_model(self):
        response = self.get("modelling/scenarios", scenario="market_down_20", loss="15")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        stress = self.get("modelling/factor-stress", scenario="market_down_20").json()
        self.assertAlmostEqual(body["named_only"]["impact"], stress["impact"])
        moves = {f["factor"]: f for f in body["linked"]["factors"]}
        self.assertEqual(moves["Mkt-RF"]["source"], "set")
        self.assertAlmostEqual(moves["Mkt-RF"]["move"], -0.2)
        self.assertNotEqual(moves["SMB"]["source"], "set")
        betas = {x["factor"]: x["beta"] for x in self.get("modelling/factors").json()["exposures"]}
        self.assertAlmostEqual(body["impact"]["portfolio_return"], sum(betas[k] * moves[k]["move"] for k in betas))
        self.assertAlmostEqual(body["reverse"]["portfolio_return"], -0.15)
        self.assertGreater(body["reverse"]["probability_of_loss_at_least"], 0.0)
        growth = self.get("modelling/scenarios", scenario="growth_selloff").json()
        self.assertEqual(growth["factors_not_in_model"], ["MOM"])
        custom = self.get("modelling/scenarios", scenario="custom", shocks="HML:0.05").json()
        self.assertEqual(custom["scenario_label"], "Custom shocks")
        self.assertEqual(custom["disclaimer"], modelling.DISCLAIMER)

    def test_input_validation(self):
        cases = [
            ("modelling/optimisers", {"max_weight": "0"}), ("modelling/optimisers", {"max_weight": "abc"}),
            ("modelling/optimisers", {"risk_aversion": "100"}), ("modelling/optimisers", {"cost_bps": "-1"}),
            ("modelling/optimisers", {"confidence": "100"}), ("modelling/optimisers", {"turnover_limit": "500"}),
            ("modelling/optimisers", {"views": "ZZZZ:5"}), ("modelling/optimisers", {"views": "S1000"}),
            ("modelling/optimisers", {"views": "S1000:500"}), ("modelling/optimisers", {"views": "S1000>S1000:2"}),
            ("modelling/optimisers", {"views": ",".join(["S1000:1"] * 6)}),
            ("modelling/rebalancing", {}), ("modelling/rebalancing", {"optimiser": "x"}),
            ("modelling/rebalancing", {"optimiser": "risk_parity", "target": "S1000=0.1"}),
            ("modelling/rebalancing", {"target": "S1000=2"}), ("modelling/rebalancing", {"target": "NOPE=0.1"}),
            ("modelling/rebalancing", {"target": "S1000"}),
            ("modelling/rebalancing", {"target": "S1000=0.5,S1001=0.5"}),
            ("modelling/goals", {"years": "2.5"}), ("modelling/goals", {"years": "0"}),
            ("modelling/goals", {"probability": "120"}), ("modelling/goals", {"target": "-5"}),
            ("modelling/goals", {"monthly_withdrawal": "x"}),
            ("modelling/scenarios", {"scenario": "moon"}), ("modelling/scenarios", {"scenario": "custom"}),
            ("modelling/scenarios", {"scenario": "custom", "shocks": "GOLD:0.1"}),
            ("modelling/scenarios", {"scenario": "custom", "shocks": "HML:0.9"}),
            ("modelling/scenarios", {"loss": "90"}), ("modelling/scenarios", {"region": "mars"}),
        ]
        for path, params in cases:
            with self.subTest(path=path, params=params):
                self.assertEqual(self.get(path, **params).status_code, 400, self.get(path, **params).text)

    def test_composites_are_refused_as_on_the_optimisation_page(self):
        app = _App(subject_type="composite_portfolio")
        with _setting("modelling=on"):
            for path in ("modelling/optimisers", "modelling/rebalancing"):
                response = app.get(path, optimiser="risk_parity") if "rebal" in path else app.get(path)
                self.assertEqual(response.status_code, 409)
                self.assertIn("Composite", response.json()["detail"])

    @unittest.skipUnless(NODE, "Node is not installed in this test environment")
    def test_the_modelling_cards_render_the_endpoints_answers(self):
        responses = {}
        for path, params in (("modelling/optimisers", {}), ("modelling/rebalancing", {"optimiser": "minimum_variance"}),
                             ("modelling/goals", {}), ("modelling/scenarios", {"scenario": "market_down_20"})):
            response = self.get(path, **params)
            responses[path] = [response.status_code, response.json()]
        code = _page_helpers() + f"responses={json.dumps(responses)};\n" + """
(async()=>{['quant-optimisers','quant-rebalancing','quant-goals','quant-scenarios'].forEach(card);
  fields['#quant-rebal-target']='minimum_variance';fields['#quant-scen-scenario']='market_down_20';
  const before=['quant-optimisers','quant-goals'].map(id=>cards[id].hidden);
  await renderQuant('2025-06-30');const out={before,requests};['quant-optimisers','quant-rebalancing','quant-goals','quant-scenarios'].forEach(id=>out[id]=shown(id));
  console.log(JSON.stringify(out))})();
"""
        out = _node(code)
        self.assertEqual(out["before"], [True, True])
        for card_id in ("quant-optimisers", "quant-rebalancing", "quant-goals", "quant-scenarios"):
            with self.subTest(card=card_id):
                shown = out[card_id]
                self.assertFalse(shown["hidden"])
                self.assertIn(modelling.DISCLAIMER, shown["note"])
                self.assertIn("Model:", shown["model"])
                for bad in ("undefined", "NaN", "[object Object]"):
                    self.assertNotIn(bad, shown["body"] + shown["note"])
        self.assertIn("Black-Litterman", out["quant-optimisers"]["body"])
        self.assertIn("Toward: Minimum variance", out["quant-rebalancing"]["body"])
        self.assertIn("Market regimes", out["quant-goals"]["body"])
        self.assertIn("Most likely moves behind", out["quant-scenarios"]["body"])
        self.assertEqual(out["requests"], [["modelling/optimisers", "as_of=2025-06-30"],
                                           ["modelling/rebalancing", "as_of=2025-06-30&optimiser=minimum_variance"],
                                           ["modelling/goals", "as_of=2025-06-30"],
                                           ["modelling/scenarios", "as_of=2025-06-30&scenario=market_down_20"]])


class AccountPageTest(unittest.TestCase):
    """The account page is unchanged while the setting is unset, and gains only what is switched on."""

    @staticmethod
    def _render(user, section, part=None):
        from ibkr_etl import web

        return web._render_account_page(user, account_id=web.PILOT_ACCOUNT_ID, section=section, part=part)

    def test_unset_bogus_and_shadow_for_viewers_render_the_same_page(self):
        for section, part in (("performance", None), ("risk", "correlation"), ("modelling", "what-if")):
            with _setting(None):
                unset = self._render(OWNER, section, part)
                viewer_unset = self._render(VIEWER, section, part)
            with _setting("performance=yes,trading=on,risk"):
                self.assertEqual(self._render(OWNER, section, part), unset)
            with _setting(ALL_ON.replace("=on", "=shadow")):
                self.assertEqual(self._render(VIEWER, section, part), viewer_unset)
            for marker in ("quant-", "renderQuant", "quantTile", "metrics-guide#metric-skill-and-luck"):
                self.assertNotIn(marker, unset)

    def test_switched_on_pages_gain_cards_subpages_and_scripts(self):
        with _setting("risk=shadow"):
            risk = self._render(OWNER, "risk", "tail-risk")
            performance = self._render(OWNER, "performance")
        self.assertIn('id="quant-tail" hidden', risk)
        self.assertIn('href="/accounts/U16088571/risk/tail-risk" data-part-link="quant-tail" aria-current="page"', risk)
        self.assertIn('"quant-tail"', risk)  # in the subpage script's parts
        self.assertIn("void renderQuant(asOf);", risk)
        self.assertIn(".quant-zone", risk)
        self.assertNotIn('id="quant-skill"', risk)
        self.assertNotIn("/performance/skill-and-luck", performance)
        ids = re.findall(r'\bid="([^"]+)"', risk)
        self.assertEqual(sorted({x for x in ids if ids.count(x) > 1}), [])
        # The subpage script gives each card to the last subpage anchor before it, so the quant cards
        # sit after the snapshot's last card and right before the Statistics anchor card.
        order = [risk.index(f'id="{card_id}"') for card_id in (
            "crisis-scenarios-card", "quant-holdings-risk", "quant-tail", "quant-regime", "quant-correlation",
            "quant-concentration", "analytics-statistics-card", "risk-correlation")]
        self.assertEqual(order, sorted(order))

    def test_subpage_route_follows_the_setting(self):
        from fastapi import HTTPException
        from ibkr_etl import web

        with _setting(None), self.assertRaises(HTTPException) as raised:
            web.account_subpage(web.PILOT_ACCOUNT_ID, "modelling", "goals", None, OWNER)
        self.assertEqual(raised.exception.status_code, 404)
        with _setting("modelling=shadow"):
            page = web.account_subpage(web.PILOT_ACCOUNT_ID, "modelling", "goals", None, OWNER)
            self.assertIn('data-part-link="quant-goals" aria-current="page"', page)
            with self.assertRaises(HTTPException):
                web.account_subpage(web.PILOT_ACCOUNT_ID, "modelling", "goals", None, VIEWER)

    @unittest.skipUnless(NODE, "Node is not installed in this test environment")
    def test_rendered_scripts_parse_with_every_page_on(self):
        with _setting(ALL_ON):
            html = self._render(OWNER, "modelling", "scenarios")
        for code in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
            result = subprocess.run([NODE, "--check", "-"], input=code, text=True, encoding="utf-8",
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)


# The account pages and the Metrics guide as they were before this feature (commit b904a2b),
# rendered in a clean environment. Re-record with: python tests/test_web_quant.py --record
GOLDEN_PAGES = {
    "owner:metrics-guide": "ff7a38a25a7ffb9d2dd52cef45a2bb1bac0cffe227ff2e36dfc200f208f2fc51",
    "owner:modelling:": "89eff96ab37c4bb597790e53b61f040f6a0786f48b0f6337696fd82d16a97e42",
    "owner:modelling:what-if": "4b92ae04c4694e63987e187b881b1eb82c1ae2add92678baa017322be60527dd",
    "owner:overview:": "d2637fdbdb11ef1c70c5df45894c5babed20b46829df23039f215b122d82ec3d",
    "owner:performance:": "c972f825a2e3700af25acc21aec85d0816d1b784aa0f4c2084eb20df5a9dd446",
    "owner:risk:": "eebf8d19ad379c9ba2673449d16c90e06d0b23f047d3aa0aa0a644190158bd42",
    "owner:risk:correlation": "df40946a307a041779738a6245d43a32e301e7f63563a22906b7ea888da8c0b6",
    "viewer:metrics-guide": "d0c250bc38edab4b42f49acb2e3b3cb00d34e4ebb43b2774d374446bde94903c",
    "viewer:modelling:": "2f4b9fa244dc3b04aaf7720c720e3e343dafe9f8daee73089fca54cb5c5d02fe",
    "viewer:modelling:what-if": "596da603bb203445bcfb32352c20a779fe506702c2dcdddcfcf5dba392c1c703",
    "viewer:overview:": "ddaebf35d2a8d982acfa274587ebce63ddd2e26fa077e9015538752311d161ac",
    "viewer:performance:": "84680d3aa9e607b3149e6549e7cfd9e03758e713dacdf401dce0694db1195fde",
    "viewer:risk:": "0cd25b8e5e8f268340dfa389cef3bce13093effee7ceadc4b8994d2495f10c96",
    "viewer:risk:correlation": "4df86ae6aa4ef8378629e91a541131f86de93f34d2b4c83e477c85a17986ace6",
}
_RENDER = r"""
import hashlib, json, sys
from unittest import mock
from ibkr_etl import web

class Inert:
    subpages = staticmethod(lambda *a: ())
    page_cards = page_css = page_script = render_hook = staticmethod(lambda *a: "")
    guide_additions = staticmethod(lambda *a: ())

users = {"owner": {"email": "owner@example.com", "object_id": "owner-1", "role": "owner"},
         "viewer": {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer"}}
pages = [("overview", None), ("performance", None), ("risk", None), ("modelling", None), ("risk", "correlation"),
         ("modelling", "what-if")]

def hashes():
    out = {}
    for name, user in users.items():
        for section, part in pages:
            html = web._render_account_page(user, account_id=web.PILOT_ACCOUNT_ID, section=section, part=part)
            out[f"{name}:{section}:{part or ''}"] = hashlib.sha256(html.encode()).hexdigest()
        out[f"{name}:metrics-guide"] = hashlib.sha256(
            web._render_platform_page(user, section="metrics-guide").encode()).hexdigest()
    return out

real = hashes()
with mock.patch.object(web, "_web_quant", Inert):
    inert = hashes()
print(json.dumps({"real": real, "inert": inert}))
"""


def _clean_render() -> dict:
    environ = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-c", _RENDER], cwd=ROOT, env=environ, capture_output=True, text=True,
                            timeout=600)
    if result.returncode:
        raise AssertionError(result.stderr[-4000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class UnchangedPageTest(unittest.TestCase):
    def test_with_the_setting_unset_the_pages_are_byte_for_byte_as_before(self):
        rendered = _clean_render()
        # With the setting unset the quant hooks add nothing at all ...
        self.assertEqual(rendered["real"], rendered["inert"])
        # ... so the pages are today's pages, byte for byte. If something outside this feature has
        # changed the page since the hashes were recorded, re-record them (see GOLDEN_PAGES).
        if rendered["inert"] != GOLDEN_PAGES:
            self.skipTest("The account page has changed outside the quant pages since the golden hashes were "
                          "recorded; the inert comparison above still holds. Re-record GOLDEN_PAGES.")
        self.assertEqual(rendered["real"], GOLDEN_PAGES)


class GuideTest(unittest.TestCase):
    def test_guide_is_unchanged_with_no_additions(self):
        self.assertEqual(render_metrics_guide(), render_metrics_guide(()))
        with _setting(None):
            self.assertEqual(web_quant.guide_additions(OWNER), ())

    def test_switched_on_pages_add_their_sections_in_place(self):
        with _setting("performance=on,modelling=shadow"):
            viewer = render_metrics_guide(web_quant.guide_additions(VIEWER))
            owner = render_metrics_guide(web_quant.guide_additions(OWNER))
        base = render_metrics_guide()
        for name, anchor in quant_guide.METRIC_ANCHORS.items():
            page = quant_guide.page_of(name)
            later = name in quant_guide.PHASE3_SECTION_OF  # only with QUANT_ANALYTICS_PHASE3 (Phase3PageAndGuideTest)
            self.assertEqual(viewer.count(f'id="{anchor}"'), 1 if page == "performance" and not later else 0, name)
            self.assertEqual(owner.count(f'id="{anchor}"'),
                             1 if page in ("performance", "modelling") and not later else 0, name)
        self.assertLess(owner.index('id="guide-benchmark"'), owner.index('id="guide-quant-performance"'))
        self.assertLess(owner.index('id="guide-quant-performance"'), owner.index('id="guide-holdings"'))
        self.assertLess(owner.index('id="guide-modelling-portfolio"'), owner.index('id="guide-quant-modelling"'))
        self.assertLess(owner.index('id="guide-quant-modelling"'), owner.index('id="guide-cash-trades"'))
        total = len(metrics_guide.METRIC_ANCHORS) + len(quant_guide.DEFINITIONS["performance"]) + len(
            quant_guide.DEFINITIONS["modelling"])
        self.assertIn(f"Showing all {total} definitions.", owner)
        self.assertEqual(owner.count('class="metric-copy"'), total)
        self.assertIn("<code>quant-analytics-v1</code>", owner)
        self.assertIn("<code>quant-goals-v1</code>", owner)
        self.assertNotIn("<code>quant-goals-v1</code>", viewer)
        # Every "See also" link lands on an entry on the same page.
        for html in (viewer, owner, base):
            ids = set(re.findall(r'<article id="([^"]+)"', html))
            self.assertLessEqual(set(re.findall(r'<a href="#(metric-[^"]+)"', html)), ids)

    def test_entries_follow_the_guide_style(self):
        labels = {f"{page.title()} › {label}" for page, parts in web_quant.QUANT_SUBPAGES.items() for _, label, _ in parts}
        for page, definitions in quant_guide.DEFINITIONS.items():
            for metric in definitions:
                with self.subTest(name=metric.name):
                    self.assertTrue(metric.meaning and metric.calculation and metric.interpretation)
                    self.assertTrue(quant_guide.CLIENT_CALCULATIONS[metric.name])
                    self.assertIn(quant_guide.LOCATIONS[metric.name], labels)
                    self.assertTrue(quant_guide.LOCATIONS[metric.name].startswith(page.title()))
                    self.assertIn("not a forecast, a guarantee or investment advice", metric.interpretation)
        self.assertFalse(set(quant_guide.METRIC_ANCHORS.values()) & set(metrics_guide.METRIC_ANCHORS.values()))

    def test_every_card_heading_links_to_its_guide_entry(self):
        for page, cards in web_quant.QUANT_CARDS.items():
            links = re.findall(r'class="metric-guide-heading-link" href="/metrics-guide#([^"]+)"', cards)
            self.assertEqual(len(links), len(web_quant.QUANT_SUBPAGES[page]))
            for anchor in links:
                name = next(n for n, a in quant_guide.METRIC_ANCHORS.items() if a == anchor)
                self.assertEqual(quant_guide.page_of(name), page)

    def test_every_tile_label_has_an_entry(self):
        from ibkr_etl import metrics_guide_audit as audit

        labels = re.findall(r"quantTile\('([^']+)'", web_quant.QUANT_SCRIPT)
        self.assertGreater(len(labels), 50)
        for label in labels:
            name = audit.metric_for_label(label)
            self.assertIn(name, quant_guide.METRIC_ANCHORS, label)


# ---------------------------------------------------------------- phase 3 (QUANT_ANALYTICS_PHASE3)

PHASE3_ON = "liquidity=on,factor_attribution=on,macro=on,crowding=on"


def _settings(pages: str | None, phase3: str | None):
    """Both settings, and nothing else of either from the environment."""
    from ibkr_etl import market_inputs

    environ = {k: v for k, v in os.environ.items() if k not in (web_quant.PAGES_SETTING, market_inputs.PHASE3_SETTING)}
    for key, value in ((web_quant.PAGES_SETTING, pages), (market_inputs.PHASE3_SETTING, phase3)):
        if value is not None:
            environ[key] = value
    return mock.patch.dict(os.environ, environ, clear=True)


def _phase3_payloads(volumes: bool = True, vix: bool = False) -> dict:
    """What the overnight build stores for the phase 3 sections on its own synthetic data (without
    the VIX by default, as the macro loader leaves it out until its licence is confirmed)."""
    import test_quant_snapshots as build_data
    from ibkr_etl import quant_snapshots

    data = build_data.synthetic_data(days=560)
    market = build_data.market_from_raw(build_data.synthetic_market(data, vix=vix), data)
    if not volumes:
        market["liquidity"] = {"volumes": {}}
    inputs = quant_snapshots.prepare_phase3_inputs(data, quant_snapshots.prepare_inputs(data), market)
    return {name: quant_snapshots.finite_copy(quant_snapshots.BUILDERS[name](inputs[name]))[0]
            for name in quant_snapshots.PHASE3_NAMES}


class Phase3SettingsTest(unittest.TestCase):
    def test_unset_the_pages_get_exactly_their_v1_pieces(self):
        for phase3 in (None, "", "liquidity=yes,macro"):
            with self.subTest(phase3=phase3), _settings(ALL_ON, phase3):
                for page in web_quant.PAGES:
                    self.assertEqual(web_quant.subpages(page, OWNER), web_quant.QUANT_SUBPAGES.get(page, ()))
                    self.assertEqual(web_quant.page_cards(page, OWNER), web_quant.QUANT_CARDS.get(page, ""))
                self.assertEqual(web_quant.page_script(OWNER), web_quant.QUANT_SCRIPT)
                self.assertEqual(web_quant.allowed_phase3(OWNER), ())
                self.assertEqual(web_quant.guide_additions(OWNER), (quant_guide.addition(web_quant.PAGES),))

    def test_a_section_needs_its_page_and_its_own_setting(self):
        with _settings("risk=on", PHASE3_ON):
            self.assertEqual(web_quant.allowed_phase3(VIEWER), ("liquidity", "macro", "crowding"))
            self.assertEqual([slug for slug, *_ in web_quant.subpages("risk", VIEWER)][-3:],
                             ["liquidity", "macro-exposures", "crowding"])
            self.assertEqual(web_quant.subpages("performance", VIEWER), ())  # its page is off
            self.assertIn(web_quant.PHASE3_SCRIPT, web_quant.page_script(VIEWER))
        with _settings(None, PHASE3_ON):
            for page in web_quant.PAGES:
                self.assertEqual((web_quant.subpages(page, OWNER), web_quant.page_cards(page, OWNER)), ((), ""))
            self.assertEqual(web_quant.page_script(OWNER), "")
        with _settings("performance=on,risk=shadow", "factor_attribution=shadow,liquidity=on,crowding=off"):
            self.assertEqual(web_quant.allowed_phase3(OWNER), ("factor_attribution", "liquidity"))
            self.assertEqual(web_quant.allowed_phase3(VIEWER), ())  # shadow on either side is owners only
            self.assertEqual(web_quant.page_script(VIEWER), web_quant.QUANT_SCRIPT)
            self.assertIn('id="quant-factor-attribution" hidden', web_quant.page_cards("performance", OWNER))
            self.assertNotIn("quant-factor-attribution", web_quant.page_cards("performance", VIEWER))
            self.assertNotIn("quant-crowding", web_quant.page_cards("risk", OWNER))

    def test_slugs_and_card_ids_are_new_and_each_card_is_once(self):
        from ibkr_etl import web

        for section, (slug, label, anchor) in web_quant.PHASE3_SUBPAGES.items():
            page = web_quant.PHASE3_SECTIONS[section][0]
            taken = {s for s, *_ in web.ACCOUNT_SUBPAGES[page]} | {s for s, *_ in web_quant.QUANT_SUBPAGES[page]}
            self.assertNotIn(slug, taken, section)
            self.assertEqual(web_quant.PHASE3_CARDS[section].count(f'id="{anchor}" hidden'), 1)
            for cards in web_quant.QUANT_CARDS.values():
                self.assertNotIn(f'id="{anchor}"', cards)
            self.assertIn(f"['{section}','{anchor}',", web_quant.PHASE3_SCRIPT)
            self.assertEqual(web_quant.PHASE3_SECTIONS[section][1], quant_snapshots_model(section))


def quant_snapshots_model(section: str) -> str:
    from ibkr_etl import quant_snapshots

    return quant_snapshots.SECTION_BY_NAME[section].model_id


class Phase3RouteTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.payloads = _phase3_payloads()
        for section, payload in self.payloads.items():
            self.app.connection.snapshots[section] = [_row(section, payload, "2026-10-16")]

    def test_every_phase_3_route_is_404_and_reads_nothing_unless_both_settings_allow_it(self):
        for pages, phase3 in ((None, None), (ALL_ON, None), (None, PHASE3_ON), ("performance=on", "liquidity=on"),
                              ("risk=on", "factor_attribution=on")):
            with self.subTest(pages=pages, phase3=phase3), _settings(pages, phase3):
                for section in web_quant.PHASE3_SECTIONS:
                    if pages and phase3 and web_quant.phase3_allowed(section, OWNER):
                        continue
                    response = self.app.get(f"quant/{section}")
                    self.assertEqual((response.status_code, response.json()), (404, {"detail": "Not Found"}))
        self.assertEqual(self.app.connection.quant_reads(), 0)

    def test_shadow_shows_owners_only_and_on_shows_everyone(self):
        with _settings(ALL_ON, "liquidity=shadow,macro=on"):
            owner = self.app.get("quant/liquidity", as_of="2026-10-16")
            macro = self.app.get("quant/macro")
            self.app.user = dict(VIEWER)
            viewer = self.app.get("quant/liquidity")
            viewer_macro = self.app.get("quant/macro")
        self.assertEqual(owner.status_code, 200, owner.text)
        self.assertEqual((owner.json()["page_mode"], owner.json()["model_id"]), ("shadow", "liquidity"))
        self.assertEqual(owner.json()["results"], self.payloads["liquidity"]["results"])
        self.assertEqual(viewer.status_code, 404)
        self.assertEqual((macro.json()["page_mode"], viewer_macro.status_code), ("on", 200))
        with _settings(ALL_ON.replace("risk=on", "risk=shadow"), "macro=on"):
            self.assertEqual(self.app.get("quant/macro").status_code, 404)  # a viewer, and the page is shadow
            self.app.user = dict(OWNER)
            self.assertEqual(self.app.get("quant/macro").json()["page_mode"], "shadow")

    def test_a_date_before_any_row_says_so(self):
        with _settings(ALL_ON, PHASE3_ON):
            response = self.app.get("quant/crowding", as_of="2026-01-02")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"],
                         "No crowding figures have been built for this account on or before this date yet.")


@unittest.skipUnless(NODE, "Node is not installed in this test environment")
class Phase3RenderingTest(unittest.TestCase):
    """The phase 3 cards render what the overnight build stores, a figure in every tile."""

    @classmethod
    def _render(cls, payloads: dict) -> tuple[dict, dict]:
        app = _App()
        for section, payload in payloads.items():
            app.connection.snapshots[section] = [_row(section, payload, "2026-10-16")]
        with _settings(ALL_ON, PHASE3_ON):
            responses = {f"quant/{section}": [200, app.get(f"quant/{section}").json()] for section in payloads}
        code = _page_helpers() + web_quant.PHASE3_SCRIPT + f"responses={json.dumps(responses)};\n" + """
(async()=>{const out={};for(const [section,id,render] of quantSections){if(!responses[`quant/${section}`])continue;
  card(id);await quantLoadSection(section,id,render,'2026-10-16');out[section]=shown(id)}
  console.log(JSON.stringify(out))})();
"""
        return _node(code), responses

    @classmethod
    def setUpClass(cls):
        cls.out, cls.responses = cls._render(_phase3_payloads())
        cls.bare, _ = cls._render({"liquidity": _phase3_payloads(volumes=False)["liquidity"]})
        cls.vix, _ = cls._render({"macro": _phase3_payloads(vix=True)["macro"]})

    def _tiles(self, shown):
        self.assertFalse(shown["hidden"])
        for text in (shown["body"], shown["note"], shown["model"]):
            for bad in ("undefined", "NaN", "[object Object]", "null"):
                self.assertNotIn(bad, text)
        tiles = re.findall(r'<div class="label">([^<]*)</div><div class="value">(.*?)</div></div>', shown["body"])
        self.assertTrue(tiles)
        self.assertEqual([label for label, value in tiles if re.sub(r"<[^>]+>", "", value).strip() in ("", "—")], [])
        return dict(tiles)

    def test_every_tile_has_a_figure(self):
        self.assertEqual(set(self.out), set(web_quant.PHASE3_SECTIONS))
        for section in web_quant.PHASE3_SECTIONS:
            with self.subTest(section=section):
                shown = self.out[section]
                self._tiles(shown)
                self.assertNotIn("Not worked out", shown["note"])
                self.assertIn("Model:", shown["model"])
                self.assertIn(self.responses[f"quant/{section}"][1]["model_name"], shown["model"])

    def test_known_figures_appear(self):
        results = {s: self.responses[f"quant/{s}"][1]["results"] for s in web_quant.PHASE3_SECTIONS}
        liquidity = self._tiles(self.out["liquidity"])
        self.assertRegex(liquidity["Sellable in 1 day"], r"^\d+(\.\d+)?%$")
        self.assertGreater(results["liquidity"]["days_to_liquidate"]["sellable_within"]["1"], 0)
        self.assertEqual(liquidity["Holdings with volume data"], "7 of 8")  # synthetic_market leaves one out
        self.assertIn("No volume data: SYN8.", self.out["liquidity"]["note"])
        crowding = self._tiles(self.out["crowding"])
        self.assertTrue(crowding["Most similar fund"].startswith("Echo Management · 1.00"))
        self.assertIn("Not covered", self.out["crowding"]["note"])
        macro = self._tiles(self.out["macro"])
        self.assertEqual(macro["Weeks in the macro model"],
                         str(results["macro"]["macro_exposures"]["window"]["observations"]))
        # Without the VIX (the default): no VIX tile, the risk-off week on its other parts, and why.
        self.assertNotIn("VIX +50%", macro)
        self.assertIn("Risk-off week", macro)
        self.assertIn('title="Yield −50bp, dollar +5%, Brent −20%, USD/CNH +3% together; VIX left out"',
                      self.out["macro"]["body"])
        self.assertIn("Left out of the model: VIX (not in silver macro_series for the window. VIXCLS is Cboe "
                      "copyright, so the macro series loader keeps it only when "
                      "MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS).", self.out["macro"]["note"])
        self.assertIn("Shocks left out, as their series are not in the model: VIX +50%.", self.out["macro"]["note"])
        factor = self._tiles(self.out["factor_attribution"])
        self.assertIn("Explained by factors (past year)", factor)
        self.assertIn("Market", self.out["factor_attribution"]["body"])

    def test_macro_with_the_vix_once_licensed(self):
        tiles = self._tiles(self.vix["macro"])
        self.assertIn("VIX +50%", tiles)
        self.assertIn('title="Yield −50bp, dollar +5%, Brent −20%, VIX +50%, USD/CNH +3% together"',
                      self.vix["macro"]["body"])
        self.assertNotIn("Left out", self.vix["macro"]["note"])
        self.assertIn("VIX", self.vix["macro"]["body"])  # its row in the table

    def test_liquidity_without_volumes_shows_the_var_and_says_why(self):
        tiles = self._tiles(self.bare["liquidity"])
        self.assertEqual(tiles["Holdings with volume data"], "0 of 8")
        self.assertIn("Liquidity-adjusted VaR (99%)", tiles)
        self.assertNotIn("Sellable in 1 day", tiles)
        self.assertIn("Not worked out: No holding has a daily volume", self.bare["liquidity"]["note"])


class Phase3PageAndGuideTest(unittest.TestCase):
    @staticmethod
    def _render(user, section, part=None):
        from ibkr_etl import web

        return web._render_account_page(user, account_id=web.PILOT_ACCOUNT_ID, section=section, part=part)

    def test_unset_the_account_pages_and_the_guide_are_as_with_phase_3_absent(self):
        for section, part in (("performance", None), ("risk", None), ("risk", "correlation")):
            with _settings(ALL_ON, None):
                unset = self._render(OWNER, section, part)
            with _settings(ALL_ON, "liquidity=shadow"):
                self.assertEqual(self._render(VIEWER, section, part).count("quant-liquidity"), 0)
            with _settings(ALL_ON, "liquidity=bogus,macro"):
                self.assertEqual(self._render(OWNER, section, part), unset)
            for marker in ("quant-liquidity", "quantRenderMacro", "quant-factor-attribution", "Fund ownership overlap"):
                self.assertNotIn(marker, unset)
        with _settings(ALL_ON, None):
            unset = render_metrics_guide(web_quant.guide_additions(OWNER))
        with _settings(ALL_ON, "crowding=off"):
            self.assertEqual(render_metrics_guide(web_quant.guide_additions(OWNER)), unset)

    def test_switched_on_the_risk_page_gains_the_cards_in_order(self):
        with _settings("risk=on", "liquidity=on,macro=on,crowding=shadow"):
            risk = self._render(OWNER, "risk", "liquidity")
            viewer = self._render(VIEWER, "risk")
        self.assertIn('href="/accounts/U16088571/risk/liquidity" data-part-link="quant-liquidity" aria-current="page"',
                      risk)
        ids = re.findall(r'\bid="([^"]+)"', risk)
        self.assertEqual(sorted({x for x in ids if ids.count(x) > 1}), [])
        order = [risk.index(f'id="{card_id}"') for card_id in (
            "quant-concentration", "quant-liquidity", "quant-macro", "quant-crowding", "analytics-statistics-card")]
        self.assertEqual(order, sorted(order))
        self.assertIn("quantRenderCrowding", risk)
        self.assertIn('id="quant-macro"', viewer)
        self.assertNotIn('id="quant-crowding"', viewer)

    @unittest.skipUnless(NODE, "Node is not installed in this test environment")
    def test_rendered_scripts_parse_with_phase_3_on(self):
        with _settings(ALL_ON, PHASE3_ON):
            html = self._render(OWNER, "performance", "factor-attribution")
        self.assertIn('data-part-link="quant-factor-attribution" aria-current="page"', html)
        for code in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
            result = subprocess.run([NODE, "--check", "-"], input=code, text=True, encoding="utf-8",
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_guide_places_each_section_s_entries_after_its_page(self):
        with _settings("performance=on,risk=on", "factor_attribution=on,liquidity=on,crowding=shadow"):
            owner = render_metrics_guide(web_quant.guide_additions(OWNER))
            viewer = render_metrics_guide(web_quant.guide_additions(VIEWER))
        for name, section in quant_guide.PHASE3_SECTION_OF.items():
            anchor = quant_guide.METRIC_ANCHORS[name]
            self.assertEqual(owner.count(f'id="{anchor}"'), 0 if section == "macro" else 1, name)
            self.assertEqual(viewer.count(f'id="{anchor}"'), 0 if section in ("macro", "crowding") else 1, name)
        self.assertLess(owner.index('id="guide-quant-risk"'), owner.index('id="guide-quant-risk-phase3"'))
        self.assertLess(owner.index('id="guide-quant-performance"'), owner.index('id="guide-quant-performance-phase3"'))
        self.assertIn("phase 3: Factor attribution, Liquidity, Crowding", owner)
        for html in (owner, viewer):
            ids = set(re.findall(r'<article id="([^"]+)"', html))
            self.assertLessEqual(set(re.findall(r'<a href="#(metric-[^"]+)"', html)), ids)

    def test_entries_follow_the_guide_style(self):
        for section, definitions in quant_guide.PHASE3_DEFINITIONS.items():
            page = web_quant.PHASE3_SECTIONS[section][0]
            label = web_quant.PHASE3_SUBPAGES[section][1]
            for metric in definitions:
                with self.subTest(name=metric.name):
                    self.assertTrue(metric.meaning and metric.calculation and metric.interpretation)
                    self.assertTrue(quant_guide.CLIENT_CALCULATIONS[metric.name])
                    self.assertEqual(quant_guide.LOCATIONS[metric.name], f"{page.title()} › {label}")
                    self.assertEqual(quant_guide.page_of(metric.name), page)
                    self.assertIn("not a forecast, a guarantee or investment advice", metric.interpretation)

    def test_every_card_heading_and_tile_label_has_an_entry(self):
        from ibkr_etl import metrics_guide_audit as audit

        for section, cards in web_quant.PHASE3_CARDS.items():
            links = re.findall(r'class="metric-guide-heading-link" href="/metrics-guide#([^"]+)"', cards)
            self.assertEqual(len(links), 1)
            name = next(n for n, a in quant_guide.METRIC_ANCHORS.items() if a == links[0])
            self.assertEqual(quant_guide.PHASE3_SECTION_OF[name], section)
        labels = re.findall(r"quantTile\('([^']+)'", web_quant.PHASE3_SCRIPT)
        self.assertGreater(len(labels), 25)
        for label in labels:
            self.assertIn(audit.metric_for_label(label), quant_guide.PHASE3_SECTION_OF, label)
        self.assertTrue(audit.audit().clean)


if __name__ == "__main__":
    if "--record" in sys.argv:
        print(json.dumps(_clean_render()["inert"], indent=1, sort_keys=True))
    else:
        unittest.main()
