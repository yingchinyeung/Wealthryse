"""Holdings v1: enrichment, combined aggregation and endpoint authorization."""
from __future__ import annotations

import json
import re
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException

from ibkr_etl import web
from ibkr_etl.holdings_view import (
    WEIGHT_BASIS_GROSS,
    WEIGHT_BASIS_NAV,
    combine_holdings,
    enrich_holdings,
)


def holding(conid, currency, value, cost=None, pnl=None, quantity="10", day="2025-01-21", sector=None):
    return {
        "snapshot_date": day, "conid": conid, "symbol": conid, "description": f"{conid} Inc",
        "currency": currency, "quantity_text": quantity, "mark_price_text": "1",
        "market_value_text": value, "cost_basis_text": cost, "unrealized_pnl_text": pnl,
        "sector": sector,
    }


def fx(day, source, target, rate):
    return {"rate_date": day, "from_currency": source, "to_currency": target, "rate_text": rate}


class HoldingsEnrichmentTests(unittest.TestCase):
    def test_identity_direct_and_reciprocal_fx_use_dated_rate(self):
        rows = [holding("A", "USD", "100", "80", "20"),
                holding("B", "HKD", "780", "390", "390"),
                holding("C", "EUR", "50", "40", "10")]
        rates = [fx("2025-01-20", "HKD", "USD", "0.125"), fx("2025-01-22", "HKD", "USD", "9"),
                 fx("2025-01-21", "USD", "EUR", "0.5")]
        result = enrich_holdings(rows, reporting_currency="usd", fx_rows=rates)
        a, b, c = result["holdings"]
        self.assertEqual(a["reporting_fx"]["direction"], "identity")
        self.assertEqual(a["reporting_market_value_text"], "100")
        self.assertEqual(b["reporting_fx"], {"from": "HKD", "to": "USD", "rate": "0.125",
                                             "date": "2025-01-20", "direction": "direct"})
        self.assertEqual(float(b["reporting_market_value_text"]), 97.5)
        self.assertEqual(float(b["reporting_cost_basis_text"]), 48.75)
        self.assertEqual(c["reporting_fx"]["direction"], "reciprocal")
        self.assertEqual(c["reporting_fx"]["date"], "2025-01-21")
        self.assertEqual(float(c["reporting_market_value_text"]), 100.0)
        self.assertEqual(float(c["reporting_unrealized_pnl_text"]), 20.0)
        self.assertEqual(result["reporting_currency"], "USD")
        self.assertEqual(result["fx_missing_conids"], [])

    def test_missing_or_invalid_fx_flags_row_without_raising(self):
        rows = [holding("A", "USD", "100"), holding("J", "JPY", "1000", "900"),
                holding("G", "GBP", "10")]
        rates = [fx("2025-01-21", "GBP", "USD", "1.2"), fx("2025-01-21", "GBP", "USD", "1.3"),
                 fx("2025-01-25", "JPY", "USD", "0.007")]  # after the holding date only
        result = enrich_holdings(rows, reporting_currency="USD", fx_rows=rates)
        by_id = {row["conid"]: row for row in result["holdings"]}
        for conid, flag in (("J", "fx_missing"), ("G", "fx_invalid")):
            row = by_id[conid]
            self.assertTrue(row["fx_missing"])
            self.assertIn(flag, row["flags"])
            self.assertIsNone(row["reporting_market_value_text"])
            self.assertIsNone(row["reporting_cost_basis_text"])
            self.assertIsNone(row["weight"])
        self.assertEqual(by_id["J"]["market_value_text"], "1000")
        self.assertEqual(result["fx_missing_conids"], ["G", "J"])
        self.assertEqual(result["weight_basis"], WEIGHT_BASIS_GROSS)
        self.assertEqual(by_id["A"]["weight"], 1.0)

    def test_unavailable_fx_source_flags_every_foreign_row(self):
        result = enrich_holdings([holding("A", "USD", "1"), holding("B", "HKD", "1")],
                                 reporting_currency="HKD", fx_unavailable_reason="fx_source_unavailable")
        self.assertTrue(all(row["fx_missing"] for row in result["holdings"]))
        self.assertIn("fx_source_unavailable", result["holdings"][0]["flags"])

    def test_weight_uses_nav_then_falls_back_to_gross_holdings(self):
        rows = [holding("A", "USD", "300"), holding("S", "USD", "-100")]
        nav = enrich_holdings(rows, reporting_currency="USD", nav_total="1000")
        self.assertEqual(nav["weight_basis"], WEIGHT_BASIS_NAV)
        self.assertEqual([row["weight"] for row in nav["holdings"]], [0.3, -0.1])
        for missing in (None, "", "0"):
            gross = enrich_holdings(rows, reporting_currency="USD", nav_total=missing)
            self.assertEqual(gross["weight_basis"], WEIGHT_BASIS_GROSS)
            self.assertEqual(gross["gross_holdings_text"], "400")
            self.assertEqual([row["weight"] for row in gross["holdings"]], [0.75, -0.25])
        self.assertIsNone(enrich_holdings([], reporting_currency="USD")["weight_basis"])

    def test_pnl_percent_sector_and_derived_pnl(self):
        rows = [holding("A", "USD", "150", "100", "50", sector="Technology"),
                holding("S", "USD", "-80", "-100", "20"),
                holding("D", "USD", "90", "100"),
                holding("N", "USD", "10", None, None),
                holding("Z", "USD", "5", "0", "5")]
        by_id = {row["conid"]: row for row in enrich_holdings(rows, reporting_currency="USD")["holdings"]}
        self.assertEqual(by_id["A"]["unrealized_pnl_pct"], 0.5)
        self.assertEqual(by_id["A"]["sector"], "Technology")
        self.assertEqual(by_id["S"]["unrealized_pnl_pct"], 0.2)
        self.assertEqual(by_id["S"]["sector"], "Unclassified")
        self.assertEqual(by_id["D"]["unrealized_pnl_text"], "-10")
        self.assertEqual(by_id["D"]["unrealized_pnl_source"], "derived")
        self.assertEqual(by_id["D"]["unrealized_pnl_pct"], -0.1)
        self.assertIsNone(by_id["N"]["unrealized_pnl_pct"])
        self.assertIn("missing_cost_basis", by_id["N"]["flags"])
        self.assertIsNone(by_id["Z"]["unrealized_pnl_pct"])

    def test_source_rows_are_not_mutated(self):
        row = holding("B", "HKD", "780")
        enrich_holdings([row], reporting_currency="USD", fx_rows=[fx("2025-01-20", "USD", "HKD", "7.8")])
        self.assertNotIn("reporting_market_value_text", row)


class CombinedAggregationTests(unittest.TestCase):
    def account(self, account_id, rows, rates=()):
        return {"account_id": account_id, "display_name": account_id.title(),
                "holdings": enrich_holdings(rows, reporting_currency="USD", fx_rows=rates)["holdings"]}

    def test_aggregates_by_conid_with_breakdown_and_gross_weight(self):
        first = self.account("u1", [holding("AAPL", "USD", "200", "150", "50", quantity="2"),
                                    holding("700", "HKD", "780", "858", "-78", quantity="100")],
                             [fx("2025-01-20", "USD", "HKD", "7.8")])
        second = self.account("u2", [holding("AAPL", "USD", "100", "90", "10", quantity="1")])
        result = combine_holdings([first, second], currency="usd")
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["weight_basis"], WEIGHT_BASIS_GROSS)
        aapl, tencent = result["holdings"]
        self.assertEqual(aapl["conid"], "AAPL")
        self.assertEqual(aapl["quantity_text"], "3")
        self.assertEqual(aapl["reporting_market_value_text"], "300")
        self.assertEqual(aapl["reporting_cost_basis_text"], "240")
        self.assertEqual(aapl["reporting_unrealized_pnl_text"], "60")
        self.assertEqual(aapl["unrealized_pnl_pct"], 0.25)
        self.assertEqual([a["account_id"] for a in aapl["accounts"]], ["u1", "u2"])
        self.assertEqual(aapl["account_count"], 2)
        self.assertAlmostEqual(float(tencent["reporting_market_value_text"]), 100.0)
        self.assertAlmostEqual(aapl["weight"], 0.75)
        self.assertAlmostEqual(tencent["weight"], 0.25)
        self.assertFalse(result["incomplete"])

    def test_missing_fx_makes_aggregate_incomplete_but_keeps_quantity(self):
        first = self.account("u1", [holding("SAP", "EUR", "100", "90", quantity="4")])
        second = self.account("u2", [holding("SAP", "EUR", "50", "40", quantity="2")],
                              [fx("2025-01-21", "EUR", "USD", "1.1")])
        row = combine_holdings([first, second], currency="USD")["holdings"][0]
        self.assertEqual(row["quantity_text"], "6")
        self.assertTrue(row["incomplete"])
        self.assertAlmostEqual(float(row["reporting_market_value_text"]), 55.0)
        self.assertIsNone(row["reporting_cost_basis_text"])
        self.assertIsNone(row["unrealized_pnl_pct"])
        self.assertTrue(row["accounts"][0]["fx_missing"])

    def test_rejects_rows_in_another_currency(self):
        account = {"account_id": "u1", "holdings": enrich_holdings(
            [holding("A", "USD", "1")], reporting_currency="HKD",
            fx_rows=[fx("2025-01-20", "USD", "HKD", "7.8")])["holdings"]}
        with self.assertRaises(ValueError):
            combine_holdings([account], currency="USD")


class _Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class _Fixture:
    """sqlite stand-in that executes the website's real SQL."""

    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.raw.executescript("""
            CREATE TABLE platform_subjects(tenant_id, subject_id, display_name, base_currency,
                reporting_currency, subject_type, history_start_date, lifecycle_status, source_account_id);
            CREATE TABLE subject_feature_states(tenant_id, subject_id, feature_key, feature_state);
            CREATE TABLE user_account_access(tenant_id, subject_id, entra_object_id, access_level);
            CREATE TABLE subject_calculation_definitions(subject_id, subject_type, configuration_json,
                approval_status, definition_version, created_at);
            CREATE TABLE subject_position_series(tenant_id, subject_id, snapshot_date, conid, currency,
                quantity_text, cost_basis_text, mark_price_text, market_value_text, unrealized_pnl_text);
            CREATE TABLE instruments(conid, symbol, description);
            CREATE TABLE instrument_classifications(tenant_id, conid, provider, sector);
            CREATE TABLE fx_rates(account_id, rate_date, from_currency, to_currency, rate_text);
            CREATE TABLE subject_nav_series(tenant_id, subject_id, snapshot_date, currency, total_text);
        """)
        subjects = [
            ("U1", "Visible USD", "USD", "physical_account", None),
            ("U2", "Visible HKD", "HKD", "physical_account", None),
            ("U3", "Hidden", "USD", "physical_account", None),
            ("F1", "Filtered", "USD", "filtered_portfolio", None),
        ]
        for subject_id, name, currency, kind, source in subjects:
            self.raw.execute("INSERT INTO platform_subjects VALUES ('tenant',?,?,?,?,?,'2025-01-20','active',?)",
                             (subject_id, name, currency, currency, kind, source))
            for feature in ("viewer_access", "website_analytics"):
                self.raw.execute("INSERT INTO subject_feature_states VALUES ('tenant',?,?,'active')",
                                 (subject_id, feature))
        for subject_id in ("U1", "U2", "F1"):
            self.raw.execute("INSERT INTO user_account_access VALUES ('tenant',?,'viewer-1','read')", (subject_id,))
        self.raw.execute("INSERT INTO subject_calculation_definitions VALUES ('F1','filtered_portfolio',?,'approved',1,'2025-01-01')",
                         (json.dumps({"source_account_id": "U1"}),))
        self.raw.executemany("INSERT INTO instruments VALUES (?,?,?)",
                             [("265598", "AAPL", "Apple"), ("700", "700", "Tencent"), ("14204", "SAP", "SAP SE")])
        self.raw.execute("INSERT INTO instrument_classifications VALUES ('tenant','265598','Yahoo Finance search profile','Technology')")
        self.raw.execute("INSERT INTO instrument_classifications VALUES ('tenant','700','Other provider','Ignored')")
        positions = [
            ("U1", "2025-01-20", "265598", "USD", "1", "100", "50", "100", "50"),  # older snapshot
            ("U1", "2025-01-21", "265598", "USD", "10", "1500", "200", "2000", "500"),
            ("U1", "2025-01-21", "700", "HKD", "100", "8580", "78", "7800", "-780"),
            ("U1", "2025-01-21", "999", "USD", "0", "0", "1", "0", "0"),  # closed position
            ("U2", "2025-01-21", "265598", "USD", "5", "900", "200", "1000", "100"),
            ("U2", "2025-01-21", "14204", "EUR", "3", "300", "100", "300", "0"),
            ("U3", "2025-01-21", "265598", "USD", "1000", "1", "200", "200000", "0"),
            ("F1", "2025-01-21", "700", "HKD", "50", "4290", "78", "3900", "-390"),
        ]
        self.raw.executemany("INSERT INTO subject_position_series VALUES ('tenant',?,?,?,?,?,?,?,?,?)", positions)
        self.raw.executemany("INSERT INTO fx_rates VALUES (?,?,?,?,?)", [
            ("U1", "2025-01-20", "USD", "HKD", "7.8"),
            ("U2", "2025-01-21", "USD", "HKD", "8"),
        ])
        self.raw.executemany("INSERT INTO subject_nav_series VALUES ('tenant',?,?,?,?)", [
            ("U1", "2025-01-21", "USD", "4000"),
            ("U1", "2025-01-21", "HKD", "1"),
        ])
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        sql = sql.replace("%s", "?")
        sql = re.sub(r"(COALESCE\(NULLIF\([\w.]+, ''\), '0'\))::numeric", r"CAST(\1 AS REAL)", sql)
        sql = re.sub(r"::(?:text|numeric)", "", sql)
        return _Cursor(self.raw.execute(sql, params))


VIEWER = {"email": "viewer@example.com", "object_id": "viewer-1", "role": "viewer",
          "roles": ["viewer"], "authorization_source": "database"}


class HoldingsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.db = _Fixture()
        self.addCleanup(self.db.raw.close)

        @contextmanager
        def database():
            yield self.db

        for patcher in (patch.object(web, "TENANT_ID", "tenant"),
                        patch.object(web, "_database", database),
                        patch.object(web, "_portable_database_connection", lambda connection: connection)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_account_holdings_keep_existing_fields_and_add_enrichment(self):
        result = web.holdings("U1", user=VIEWER)
        self.assertEqual(result["snapshot_date"], "2025-01-21")
        self.assertEqual(result["weight_basis"], "nav")
        self.assertEqual(result["reporting_currency"], "USD")
        self.assertEqual(result["fx_account_id"], "U1")
        rows = {row["conid"]: row for row in result["holdings"]}
        self.assertEqual(set(rows), {"265598", "700"})
        for field in ("snapshot_date", "conid", "symbol", "description", "currency",
                      "quantity_text", "mark_price_text", "market_value_text"):
            self.assertIn(field, rows["700"])
        self.assertEqual(rows["265598"]["sector"], "Technology")
        self.assertEqual(rows["700"]["sector"], "Unclassified")
        self.assertEqual(rows["265598"]["weight"], 0.5)
        self.assertEqual(rows["700"]["reporting_fx"]["direction"], "reciprocal")
        self.assertAlmostEqual(float(rows["700"]["reporting_market_value_text"]), 1000.0)
        self.assertAlmostEqual(rows["700"]["weight"], 0.25)
        self.assertAlmostEqual(rows["700"]["unrealized_pnl_pct"], -780 / 8580)

    def test_filtered_portfolio_uses_source_account_fx_and_gross_fallback(self):
        result = web.holdings("F1", user=VIEWER)
        self.assertEqual(result["fx_account_id"], "U1")
        self.assertEqual(result["weight_basis"], "gross_holdings")
        row = result["holdings"][0]
        self.assertAlmostEqual(float(row["reporting_market_value_text"]), 500.0)
        self.assertEqual(row["weight"], 1.0)

    def test_missing_fx_does_not_fail_the_account_page(self):
        result = web.holdings("U2", user=VIEWER)
        rows = {row["conid"]: row for row in result["holdings"]}
        self.assertTrue(rows["14204"]["fx_missing"])
        self.assertIn("fx_missing", rows["14204"]["flags"])
        self.assertEqual(float(rows["265598"]["reporting_fx"]["rate"]), 8.0)
        self.assertEqual(result["fx_missing_conids"], ["14204"])

    def test_hidden_account_holdings_are_not_available(self):
        with self.assertRaises(HTTPException) as raised:
            web.holdings("U3", user=VIEWER)
        self.assertEqual(raised.exception.status_code, 404)

    def test_combined_view_only_includes_visible_physical_accounts(self):
        result = web.combined_holdings(currency="usd", user=VIEWER)
        self.assertEqual([a["account_id"] for a in result["accounts"]], ["U2", "U1"])
        self.assertNotIn("U3", json.dumps(result))
        self.assertNotIn("F1", json.dumps(result))
        data_reads = [params for sql, params in self.db.calls
                      if "FROM subject_position_series" in sql or "FROM fx_rates" in sql]
        self.assertTrue(data_reads)
        self.assertFalse(any("U3" in params or "F1" in params for params in data_reads))
        rows = {row["conid"]: row for row in result["holdings"]}
        apple = rows["265598"]
        self.assertEqual(apple["quantity_text"], "15")
        self.assertEqual(apple["reporting_market_value_text"], "3000")
        self.assertEqual(apple["reporting_cost_basis_text"], "2400")
        self.assertEqual(apple["account_count"], 2)
        self.assertAlmostEqual(float(rows["700"]["reporting_market_value_text"]), 1000.0)
        self.assertEqual(rows["700"]["quantity_text"], "100")
        self.assertTrue(rows["14204"]["incomplete"])
        self.assertTrue(result["incomplete"])
        self.assertAlmostEqual(apple["weight"], 0.75)

    def test_combined_view_converts_each_account_with_its_own_fx(self):
        result = web.combined_holdings(currency="HKD", user=VIEWER)
        apple = {row["conid"]: row for row in result["holdings"]}["265598"]
        by_account = {a["account_id"]: a for a in apple["accounts"]}
        self.assertEqual(float(by_account["U1"]["reporting_fx"]["rate"]), 7.8)
        self.assertEqual(float(by_account["U2"]["reporting_fx"]["rate"]), 8.0)
        self.assertAlmostEqual(float(apple["reporting_market_value_text"]), 2000 * 7.8 + 1000 * 8)

    def test_combined_view_for_unentitled_viewer_is_empty(self):
        stranger = {**VIEWER, "object_id": "someone-else"}
        result = web.combined_holdings(user=stranger)
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["holdings"], [])
        self.assertFalse(any("subject_position_series" in sql for sql, _ in self.db.calls))

    def test_combined_view_rejects_invalid_currency(self):
        for value in ("US", "U$D", "USDX"):
            with self.assertRaises(HTTPException) as raised:
                web.combined_holdings(currency=value, user=VIEWER)
            self.assertEqual(raised.exception.status_code, 400)

    def test_combined_view_honours_as_of(self):
        result = web.combined_holdings(currency="USD", as_of="2025-01-20", user=VIEWER)
        self.assertEqual([a["account_id"] for a in result["accounts"] if a["snapshot_date"]], ["U1"])
        self.assertEqual(result["holdings"][0]["quantity_text"], "1")

    def test_combined_page_is_linked_and_escapes_viewer(self):
        user = {"email": "<b>viewer</b>@example.com", "role": "viewer"}
        page = web.combined_holdings_page(None, user)
        self.assertIn("/api/holdings/combined", page)
        self.assertIn("&lt;b&gt;viewer", page)
        self.assertNotIn("<b>viewer", page)
        self.assertIn('href="/combined-holdings"', web._platform_navigation(user))


if __name__ == "__main__":
    unittest.main()
