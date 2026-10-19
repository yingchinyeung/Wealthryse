"""The phase 3 gold sections (liquidity, factor_attribution, macro, crowding) without a database.

The setting (QUANT_ANALYTICS_PHASE3) and the build's gating: unset, nothing more is read or
written; switched on, a section whose silver table is not there yet is skipped. The readers in
ibkr_etl.market_inputs on a fake connection (a missing table is None, inside a savepoint), the
CUSIP rule (the 13F loader's, ibkr_etl.sec_13f), and known answers for each builder: the average daily volume of a constant
volume, the days to sell a known position, factor betas and macro sensitivities recovered from
returns built from them, and an overlap of 1 for a fund holding the portfolio's own mix.
tests/test_quant_phase3_db.py runs the readers and the job on a real PostgreSQL. Synthetic data only.
"""
from __future__ import annotations

import contextlib
import json
import math
import random
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg

from ibkr_etl import market_inputs as mi
from ibkr_etl import model_cards, publish_gate
from ibkr_etl import quant_snapshots as qs
from ibkr_etl.quant.crowding import ownership_overlap
from test_quant_snapshots import (ACCOUNT, AS_OF, FILERS, business_days, market_from_raw, synthetic_data,
                                  synthetic_market)

ROOT = Path(__file__).resolve().parents[1]
ALL_ON = {mi.PHASE3_SETTING: "liquidity=on,factor_attribution=on,macro=on,crowding=on"}
OWNER, VIEWER = {"role": "owner"}, {"role": "viewer"}


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    """Answers a query by the first key found in its SQL; a relation in `missing` raises as
    PostgreSQL does before its migration has run. Savepoints are counted, and rolled back on error."""

    def __init__(self, answers=None, missing=()):
        self.answers = dict(answers or {})
        self.missing = set(missing)
        self.statements: list[tuple[str, tuple]] = []
        self.savepoints = self.rolled_back = self.commits = self.rollbacks = 0

    @contextlib.contextmanager
    def transaction(self):
        self.savepoints += 1
        try:
            yield self
        except Exception:
            self.rolled_back += 1
            raise

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        for name in self.missing:
            if re.search(rf"\bFROM {name}\b", sql):
                raise psycopg.errors.UndefinedTable(f'relation "{name}" does not exist')
        for key, rows in self.answers.items():
            if key in sql:
                return _Result(rows(params) if callable(rows) else rows)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def sql(self) -> str:
        return "\n".join(sql for sql, _ in self.statements)


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- the setting


class SettingTests(unittest.TestCase):
    def test_each_section_is_off_shadow_or_on_and_anything_else_is_off(self):
        self.assertEqual(mi.phase3_modes({}), {name: "off" for name in mi.PHASE3_SECTIONS})
        modes = mi.phase3_modes({mi.PHASE3_SETTING: " liquidity=shadow, MACRO = On ,crowding=maybe,factor_attribution,"
                                                   "desk=on,tail=on"})
        self.assertEqual(modes, {"liquidity": "shadow", "factor_attribution": "off", "macro": "on", "crowding": "off"})
        self.assertEqual(mi.phase3_modes({mi.PHASE3_SETTING: "liquidity=on,liquidity=off"})["liquidity"], "off")
        self.assertEqual(mi.enabled_sections({mi.PHASE3_SETTING: "crowding=on,liquidity=shadow,macro=off"}),
                         ("liquidity", "crowding"))
        self.assertEqual(mi.enabled_sections({}), ())

    def test_shadow_is_owners_only_and_on_is_everyone(self):
        environ = {mi.PHASE3_SETTING: "liquidity=shadow,macro=on"}
        self.assertTrue(mi.section_allowed("liquidity", OWNER, environ))
        self.assertFalse(mi.section_allowed("liquidity", VIEWER, environ))
        self.assertFalse(mi.section_allowed("liquidity", None, environ))
        self.assertTrue(mi.section_allowed("macro", VIEWER, environ))
        self.assertFalse(mi.section_allowed("crowding", OWNER, environ))
        self.assertFalse(mi.section_allowed("skill", OWNER, environ))

    def test_the_build_picks_the_switched_on_sections_and_v1_is_unchanged(self):
        self.assertEqual(qs.phase3_section_names(None, {}), ())
        self.assertEqual(qs.phase3_section_names(None, ALL_ON), qs.PHASE3_NAMES)
        self.assertEqual(qs.phase3_section_names(["macro", "skill", "liquidity"], ALL_ON), ("liquidity", "macro"))
        self.assertEqual(qs.PHASE3_NAMES, mi.PHASE3_SECTIONS)
        self.assertEqual(len(qs.SECTION_NAMES), 9)
        self.assertFalse(set(qs.PHASE3_NAMES) & set(qs.SECTION_NAMES))
        with self.assertRaises(ValueError):
            qs.section_names(["liquidity"])  # the v1 names alone

    def test_the_model_cards(self):
        # factor_attribution is the attribution card's factor_attribution engine (daily OLS with
        # Carino linking), not factor-model, which is the Modelling page's weekly factor model.
        expected = {"liquidity": "liquidity", "factor_attribution": "attribution", "macro": "macro-factors",
                    "crowding": "crowding"}
        for name, model_id in expected.items():
            with self.subTest(section=name):
                spec = qs.SECTION_BY_NAME[name]
                self.assertEqual(spec.model_id, model_id)
                self.assertTrue(model_cards.version(model_id))
        self.assertEqual({qs.SECTION_BY_NAME[name].page for name in qs.PHASE3_NAMES}, {"risk", "performance"})
        self.assertEqual(qs.SECTION_BY_NAME["factor_attribution"].page, "performance")

    def test_the_held_table_and_its_contract_accept_the_four_names(self):
        draft = (ROOT / "infra" / "medallion" / "drafts" / "quant_analytics.sql").read_text()
        listed = re.search(r"section TEXT NOT NULL CHECK \(section IN \((.*?)\)\)", draft, re.S).group(1)
        self.assertEqual(set(re.findall(r"'([a-z_]+)'", listed)), set(qs.SECTION_NAMES) | set(qs.PHASE3_NAMES))
        rules = json.loads((ROOT / "src" / "ibkr_etl" / "publish_gate_rules.json").read_text())
        found = json.dumps(rules)
        for name in qs.PHASE3_NAMES:
            self.assertIn(f"'{name}'", found)


# ---------------------------------------------------------------- identifiers


class IdentifierTests(unittest.TestCase):
    """A holding's CUSIP is ibkr_etl.sec_13f.cusip_from_isin, the 13F loader's own rule: there is
    no second copy, so holdings and filings are always matched the same way."""

    def test_a_us_share_is_matched_by_the_loaders_rule(self):
        from ibkr_etl import sec_13f

        self.assertFalse(hasattr(mi, "us_isin_to_cusip"))
        stock = {"asset_category": "STK"}
        for isin, cusip in (("US0378331005", "037833100"), ("US5949181045", "594918104"),  # Apple, Microsoft
                            (" us0378331005 ", "037833100")):
            with self.subTest(isin=isin):
                self.assertEqual(sec_13f.cusip_from_isin(isin), cusip)
                self.assertIsNone(qs._not_in_13f({**stock, "isin": isin}))
        for isin in ("GB0002634946", "KYG875721634", "HK0000069689",  # valid, but not US
                     "US0378331006",  # wrong check digit
                     "US037833100", "US03783310O5", "0US378331005", 12345):
            with self.subTest(isin=isin):
                self.assertIsNone(sec_13f.cusip_from_isin(isin))
                self.assertEqual(qs._not_in_13f({**stock, "isin": isin}), "Not a US ISIN, so not in 13F filings.")
        # Imported when the section runs: patched, the section follows it.
        with patch.object(sec_13f, "cusip_from_isin", return_value=None):
            self.assertEqual(qs._not_in_13f({**stock, "isin": "US0378331005"}), "Not a US ISIN, so not in 13F filings.")

    def test_the_website_and_v1_never_load_the_13f_loader(self):
        source = (ROOT / "src" / "ibkr_etl" / "market_inputs.py").read_text()
        self.assertNotIn("import sec_13f", source)
        self.assertNotIn("from .sec_13f", source)
        for path in ("quant_snapshots.py", "web_quant.py", "web_desk.py"):
            text = (ROOT / "src" / "ibkr_etl" / path).read_text()
            top = [line for line in text.splitlines() if line.startswith(("import ", "from ")) and "sec_13f" in line]
            self.assertEqual(top, [], path)


# ---------------------------------------------------------------- the readers


class ReaderTests(unittest.TestCase):
    READERS = {
        "daily_volumes": lambda c: mi.read_daily_volumes(c, tenant_id="t", instrument_ids=["a"], start="2026-01-01",
                                                         end="2026-02-01"),
        "factor_returns": lambda c: mi.read_factor_returns(c, tenant_id="t", region="developed", start="2026-01-01",
                                                           end="2026-02-01"),
        "macro_series": lambda c: mi.read_macro_series(c, tenant_id="t", start="2026-01-01", end="2026-02-01"),
        "institutional_holdings": lambda c: mi.read_institutional_holdings(c, tenant_id="t", as_of="2026-02-01",
                                                                           cusips=["037833100"]),
    }

    def test_a_missing_table_is_none_inside_a_savepoint(self):
        for table, read in self.READERS.items():
            with self.subTest(table=table):
                connection = _Connection(missing=[table])
                self.assertIsNone(read(connection))
                self.assertEqual((connection.savepoints, connection.rolled_back), (1, 1))

    def test_other_errors_are_not_swallowed(self):
        class Broken(_Connection):
            def execute(self, sql, params=()):
                raise psycopg.errors.DivisionByZero("division by zero")

        with self.assertRaises(psycopg.errors.DivisionByZero):
            mi.read_macro_series(Broken(), tenant_id="t", start="2026-01-01", end="2026-02-01")

    def test_the_catalog_says_whether_a_table_is_there(self):
        connection = _Connection({"pg_catalog.pg_class": lambda params: [{"present": params == ("daily_volumes",)}]})
        self.assertTrue(mi.table_exists(connection, "daily_volumes"))
        self.assertFalse(mi.table_exists(connection, "macro_series"))
        self.assertIn("'silver', 'public'", connection.statements[0][0])

    def test_daily_volumes_skip_zero_and_missing_days(self):
        connection = _Connection({"FROM daily_volumes": [
            {"instrument_id": "a", "price_date": "2026-01-02", "volume": 100.0},
            {"instrument_id": "a", "price_date": "2026-01-05", "volume": 0.0},
            {"instrument_id": "a", "price_date": "2026-01-06", "volume": float("nan")},
            {"instrument_id": "b", "price_date": "2026-01-06", "volume": 50}]})
        found = mi.read_daily_volumes(connection, tenant_id="t", instrument_ids=["b", "a", "a", ""],
                                      start="2026-01-01", end="2026-02-01")
        self.assertEqual(found, {"a": {"2026-01-02": 100.0}, "b": {"2026-01-06": 50.0}})
        self.assertEqual(connection.statements[0][1], ("t", ["a", "b"], "2026-01-01", "2026-02-01"))
        self.assertIn("volume > 0", connection.statements[0][0])
        self.assertEqual(mi.read_daily_volumes(_Connection(), tenant_id="t", instrument_ids=[], start="x", end="y"),
                         {})

    def test_a_holding_s_volumes_are_read_by_its_conid_never_by_symbol(self):
        # The holdings' fetch stores each holding under its IBKR conid; only the benchmarks' rows use
        # market_data_prices' ids. The read is daily_volumes alone, by conid.
        for name in ("volume_candidates", "read_symbol_volumes", "SYMBOL_VOLUMES_SQL"):
            self.assertFalse(hasattr(mi, name), name)
        self.assertNotIn("market_data_prices", mi.DAILY_VOLUMES_SQL)
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}],
                                  "FROM daily_volumes": [{"instrument_id": "9001", "price_date": AS_OF, "volume": 5.0}]})
        data = {"as_of": AS_OF, "positions": [{"date": AS_OF, "conid": conid, "symbol": "SAME"}
                                              for conid in ("9002", "9001")]}
        found = qs.load_phase3_inputs(connection, tenant_id="t", subject_id=ACCOUNT, as_of=AS_OF, data=data,
                                      sections=["liquidity"])
        self.assertEqual(found, {"liquidity": {"volumes": {"9001": {"instrument_id": "9001",
                                                                    "volumes": {AS_OF: 5.0}}}}})
        (sql, parameters), = [(sql, parameters) for sql, parameters in connection.statements
                              if "FROM daily_volumes" in sql]
        self.assertEqual(parameters, ("t", ["9001", "9002"], _shift(AS_OF, -qs.VOLUME_WINDOW_DAYS), AS_OF))
        self.assertNotIn("instruments", connection.sql().replace("daily_volumes", ""))  # no symbol lookup

    def test_factor_returns_are_kenneth_french_s_latest_vintage_for_the_region(self):
        rows = [{"factor": f, "return_date": d, "value": v, "source": "kenneth-french", "vintage": vintage}
                for f, d, v, vintage in (("Mkt-RF", "2026-01-02", 0.01, "2026-02-03"),
                                         ("RF", "2026-01-02", 0.0002, "2026-02-03"),
                                         ("Mkt-RF", "2026-01-05", float("inf"), "2026-01-30"),
                                         ("SMB", "2026-01-05", -0.002, "2026-01-30"))]
        connection = _Connection({"FROM factor_returns": rows})
        found = mi.read_factor_returns(connection, tenant_id="t", region="developed", start="2026-01-01",
                                       end="2026-02-01")
        self.assertEqual(found, {"region": "developed",
                                 "factors": {"2026-01-02": {"Mkt-RF": 0.01, "RF": 0.0002}, "2026-01-05": {"SMB": -0.002}},
                                 "sources": {"kenneth-french": 3}, "vintages": ["2026-01-30", "2026-02-03"]})
        sql, params = connection.statements[0]
        self.assertEqual(params, ("t", "developed", "kenneth-french", "2026-01-01", "2026-02-01"))
        self.assertIn("DISTINCT ON (factor, return_date)", sql)
        self.assertIn("vintage DESC", sql)
        empty = mi.read_factor_returns(_Connection({"FROM factor_returns": []}), tenant_id="t", region="us",
                                       start="x", end="y")
        self.assertEqual(empty, {"region": "us", "factors": {}, "sources": {}, "vintages": None})

    def test_macro_series_are_the_latest_vintage_with_units_and_source(self):
        rows = [{"series_id": "DGS10", "observation_date": "2026-01-02", "value": 4.1, "units": "percent",
                 "source": "fred", "vintage": "2026-01-05"},
                {"series_id": "DGS10", "observation_date": "2026-01-05", "value": None, "units": "percent",
                 "source": "fred", "vintage": "2026-01-05"},
                {"series_id": "USDCNH", "observation_date": "2026-01-02", "value": "7.1", "units": "cnh_per_usd",
                 "source": "yahoo-chart", "vintage": "2026-01-03"}]
        connection = _Connection({"FROM macro_series": rows})
        found = mi.read_macro_series(connection, tenant_id="t", start="2026-01-01", end="2026-02-01")
        self.assertEqual(found, {"series": {"DGS10": {"2026-01-02": 4.1}, "USDCNH": {"2026-01-02": 7.1}},
                                 "units": {"DGS10": "percent", "USDCNH": "cnh_per_usd"},
                                 "sources": {"DGS10": "fred", "USDCNH": "yahoo-chart"}})
        self.assertEqual(connection.statements[0][1], ("t", sorted(mi.MACRO_SERIES), "2026-01-01", "2026-02-01"))

    def test_13f_filings_come_back_as_filers_and_our_stakes(self):
        filings = [{"filer_cik": "0000000002", "filer_name": "Beta", "period": "2026-06-30",
                    "accession": "b-2", "filed_at": "2026-08-20"},
                   {"filer_cik": "0000000003", "filer_name": None, "period": "2026-03-31",
                    "accession": "c-1", "filed_at": "2026-05-15"}]
        totals = [{"filer_cik": "0000000002", "positions": 3, "total_value": 600.0, "rest_value": 500.0,
                   "rest_squares": 130000.0, "rest_positions": 2}]
        stakes = [{"filer_cik": "0000000002", "cusip": "037833100", "shares": 2.0, "value_usd": 100.0}]
        connection = _Connection({"SELECT DISTINCT ON (filer_cik)": filings, "COUNT(*) AS positions": totals,
                                  "SUM(h.shares)": stakes})
        found = mi.read_institutional_holdings(connection, tenant_id="t", as_of="2026-10-16",
                                               cusips=["037833100", "037833100", None])
        self.assertEqual(found["stakes"], [{"filer_cik": "0000000002", "cusip": "037833100", "shares": 2.0,
                                            "value_usd": 100.0}])
        self.assertEqual(found["filers"][0], {"filer_cik": "0000000002", "filer_name": "Beta", "period": "2026-06-30",
                                              "accession": "b-2", "filed_at": "2026-08-20", "positions": 3,
                                              "total_value": 600.0, "rest_value": 500.0, "rest_squares": 130000.0,
                                              "rest_positions": 2})
        # A filer whose latest filing has no share stakes at all counts with nothing.
        self.assertEqual({key: found["filers"][1][key] for key in ("filer_name", "positions", "total_value")},
                         {"filer_name": "0000000003", "positions": 0, "total_value": 0.0})
        self.assertEqual(connection.savepoints, 1)  # the three queries in one savepoint
        self.assertEqual(connection.statements[0][1], ("t", "2026-10-16", "2026-10-16"))
        # The latest quarter, then sec_13f.LATEST_SQL's rule: the newest filed_at, then the highest accession.
        self.assertIn("ORDER BY filer_cik, period DESC, filed_at DESC, accession DESC", connection.statements[0][0])
        arrays = (["0000000002", "0000000003"], ["2026-06-30", "2026-03-31"], ["b-2", "c-1"])
        self.assertEqual(connection.statements[1][1], (["037833100"],) * 3 + arrays + ("t",))
        self.assertEqual(connection.statements[2][1], arrays + ("t", ["037833100"]))
        for sql, _ in connection.statements[1:]:
            self.assertIn("h.put_call = '' AND h.shares_type = 'SH' AND h.value_usd > 0", sql)
        none_yet = _Connection({"SELECT DISTINCT ON (filer_cik)": []})
        self.assertEqual(mi.read_institutional_holdings(none_yet, tenant_id="t", as_of="2026-10-16", cusips=["x"]),
                         {"filers": [], "stakes": []})
        self.assertEqual(len(none_yet.statements), 1)


# ---------------------------------------------------------------- the builders: known answers


def _tail_returns() -> list:
    global _TAIL
    if _TAIL is None:
        _TAIL = qs.prepare_inputs(synthetic_data(days=560))["tail"]["returns"]
    return _TAIL


_TAIL = None


def _liquidity(volumes: dict, **extra) -> dict:
    holdings = [{"name": "AAA", "conid": "1", "weight": 0.4, "market_value": 40000.0, "quantity": 4000.0},
                {"name": "BBB", "conid": "2", "weight": 0.6, "market_value": 60000.0, "quantity": -600.0}]
    return {"as_of": AS_OF, "holdings_date": AS_OF, "holdings": holdings, "no_holdings": None, "volumes": volumes,
            "volume_instruments": {}, "returns": _tail_returns(), "no_returns": None, **extra}


class LiquidityTests(unittest.TestCase):
    def test_a_constant_volume_and_known_positions(self):
        days = business_days(AS_OF, 25)
        payload = qs.build_liquidity(_liquidity({"AAA": {day: 10000.0 for day in days},
                                                 "BBB": {day: 300.0 for day in days}}))
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        adv = payload["results"]["average_daily_volume"]
        self.assertEqual((adv["AAA"]["average_daily_volume"], adv["AAA"]["days_used"], adv["AAA"]["complete"]),
                         (10000.0, 20, True))
        found = payload["results"]["days_to_liquidate"]
        days_of = {row["key"]: row["days"] for row in found["holdings"]}
        self.assertAlmostEqual(days_of["AAA"], 4000 / (0.2 * 10000))  # 2 days
        self.assertAlmostEqual(days_of["BBB"], 600 / (0.2 * 300))  # 10 days: a short counts by its size
        self.assertAlmostEqual(found["days_to_sell_everything"], 10.0)
        self.assertAlmostEqual(found["value_weighted_days"], (40000 * 2 + 60000 * 10) / 100000)
        self.assertAlmostEqual(found["sellable_within"]["1"], (40000 * 0.5 + 60000 * 0.1) / 100000)
        self.assertAlmostEqual(found["sellable_within"]["5"], (40000 + 60000 * 0.5) / 100000)
        self.assertAlmostEqual(found["sellable_within"]["20"], 1.0)
        self.assertEqual(found["participation"], qs.LIQUIDITY_PARTICIPATION)
        self.assertEqual(payload["inputs"]["not_covered"], [])

    def test_holidays_and_missing_days_do_not_count(self):
        days = business_days(AS_OF, 40)
        volumes = {day: (0.0 if index % 3 == 0 else 10000.0) for index, day in enumerate(days)}
        adv = qs.build_liquidity(_liquidity({"AAA": volumes}))["results"]["average_daily_volume"]["AAA"]
        self.assertEqual((adv["average_daily_volume"], adv["days_used"]), (10000.0, 20))

    def test_a_holding_without_volumes_is_listed_as_not_covered(self):
        payload = qs.build_liquidity(_liquidity({"AAA": {day: 10000.0 for day in business_days(AS_OF, 25)}}))
        self.assertTrue(payload["available"])
        self.assertEqual(qs.quality_status(payload), "pass")
        found = payload["results"]["days_to_liquidate"]
        self.assertEqual((found["no_volume_data"], found["share_without_volume_data"]), (["BBB"], 0.6))
        self.assertIsNone(found["days_to_sell_everything"])
        self.assertEqual([(row["name"], row["weight"]) for row in payload["inputs"]["not_covered"]], [("BBB", 0.6)])
        self.assertIn("No daily volume in silver (daily_volumes)", payload["inputs"]["not_covered"][0]["reason"])

    def test_with_no_volumes_at_all_the_section_still_gives_the_var(self):
        # daily_volumes holds only the benchmarks until the holdings' volumes are fetched.
        payload = qs.build_liquidity(_liquidity({}))
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "warn")
        for name in ("average_daily_volume", "days_to_liquidate"):
            self.assertFalse(payload["results"][name]["available"])
            self.assertIn("No holding has a daily volume", payload["results"][name]["reason"])
        self.assertTrue(payload["results"]["liquidity_adjusted_var"]["available"])
        self.assertEqual(len(payload["inputs"]["not_covered"]), 2)
        self.assertAlmostEqual(payload["inputs"]["not_covered_weight"], 1.0)

    def test_the_var_is_the_tail_section_s_one_day_99_forecast(self):
        data = synthetic_data(days=560)
        prepared = qs.prepare_inputs(data)
        tail = qs.build_tail(prepared["tail"])["results"]["forecasts"]
        inputs = qs.prepare_phase3_inputs(data, prepared, market_from_raw(synthetic_market(data), data))
        payload = qs.build_liquidity(inputs["liquidity"])
        lvar = payload["results"]["liquidity_adjusted_var"]
        self.assertEqual(lvar["var"], tail["levels"]["99"]["var"])
        self.assertEqual(payload["inputs"]["var_basis"], tail["basis"])
        self.assertEqual(payload["inputs"]["var_level"], 0.99)
        # No spreads in silver: the spread cost is 0 and LVaR is the VaR, every holding listed.
        self.assertEqual((lvar["liquidity_cost"], lvar["liquidity_adjusted_var"]), (0.0, lvar["var"]))
        self.assertEqual(sorted(lvar["no_spread_data"]), sorted(h["name"] for h in inputs["liquidity"]["holdings"]))


class FactorAttributionTests(unittest.TestCase):
    BETAS = {"Mkt-RF": 1.1, "SMB": 0.4, "HML": -0.3, "RMW": 0.2, "CMA": -0.1, "MOM": 0.25}
    ALPHA = 0.0002

    def _inputs(self, *, drop=()):
        rng = random.Random(3)
        days = business_days(AS_OF, 300)
        factors = {day: {"Mkt-RF": rng.gauss(0.0004, 0.01), "SMB": rng.gauss(0.0, 0.005), "HML": rng.gauss(0.0, 0.005),
                         "RMW": rng.gauss(0.0, 0.004), "CMA": rng.gauss(0.0, 0.004), "MOM": rng.gauss(0.0, 0.006),
                         "RF": 0.00015} for day in days}
        returns = {day: row["RF"] + self.ALPHA + sum(beta * row[name] for name, beta in self.BETAS.items())
                   + rng.gauss(0.0, 0.0005) for day, row in factors.items()}
        for row in factors.values():
            for name in drop:
                row.pop(name, None)
        return {"as_of": AS_OF, "region": "developed", "region_label": "Developed markets", "returns": returns,
                "factors": factors, "sources": {"kenneth-french": 7 * len(days)},
                "vintages": ["2026-10-01", "2026-10-15"]}

    def test_known_betas_and_alpha_are_recovered_in_both_periods(self):
        payload = qs.build_factor_attribution(self._inputs())
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        expected = {"ytd": len([d for d in business_days(AS_OF, 300) if d >= "2026-01-01"]),
                    "past_year": len([d for d in business_days(AS_OF, 300) if d >= _shift(AS_OF, -365)])}
        for period, count in expected.items():
            with self.subTest(period=period):
                found = payload["results"][period]
                self.assertEqual(found["observations"], count)
                self.assertEqual(payload["inputs"][period]["observations"], count)
                betas = {row["factor"]: row["beta"] for row in found["exposures"]}
                self.assertEqual(list(betas), list(self.BETAS))
                for name, beta in self.BETAS.items():
                    self.assertAlmostEqual(betas[name], beta, delta=0.05, msg=name)
                self.assertAlmostEqual(found["alpha"]["per_period"], self.ALPHA, delta=0.00015)
                self.assertGreater(found["r_squared"], 0.95)
                # The parts add up to the compounded return over cash.
                self.assertAlmostEqual(found["factors_cumulative"] + found["alpha"]["cumulative_contribution"]
                                       + found["residual"]["cumulative_contribution"],
                                       found["cumulative_excess_return"], places=10)
        self.assertEqual(payload["inputs"]["factors"], list(self.BETAS))
        self.assertEqual(payload["inputs"]["ytd"]["start"], "2026-01-01")
        self.assertEqual(payload["inputs"]["past_year"]["start"], _shift(AS_OF, -365))

    def test_without_momentum_the_five_factors_are_used(self):
        payload = qs.build_factor_attribution(self._inputs(drop=("MOM",)))
        self.assertEqual(payload["inputs"]["factors"], ["Mkt-RF", "SMB", "HML", "RMW", "CMA"])
        self.assertTrue(payload["available"])

    def test_without_the_market_factor_it_says_why(self):
        payload = qs.build_factor_attribution(self._inputs(drop=("Mkt-RF",)))
        self.assertFalse(payload["available"])
        self.assertIn("no Developed markets market factor returns", payload["reason"])


class MacroTests(unittest.TestCase):
    # Portfolio return per bp (the yield and the curve) or per unit log return (the others).
    TRUE = {"DGS10": -0.0002, "DGS10-DGS2": 0.0001, "DTWEXBGS": -0.3, "DCOILBRENTEU": 0.1, "VIXCLS": -0.05,
            "USDCNH": 0.2}

    def _inputs(self, *, drop=()):
        """Daily rows where the series move only on Fridays and the portfolio only then too, so each
        weekly observation is one known regression row."""
        rng = random.Random(9)
        days = business_days(AS_OF, 3 * 260)
        level = {"DGS10": 4.0, "DGS2": 3.5, "DTWEXBGS": 120.0, "DCOILBRENTEU": 80.0, "VIXCLS": 18.0, "USDCNH": 7.2}
        levels = {name: {} for name in level}
        returns = {}
        for day in days:
            value = 0.0
            if date.fromisoformat(day).weekday() == 4:
                ten, two = rng.gauss(0.0, 10.0), rng.gauss(0.0, 10.0)
                logs = {"DTWEXBGS": rng.gauss(0.0, 0.01), "DCOILBRENTEU": rng.gauss(0.0, 0.04),
                        "VIXCLS": rng.gauss(0.0, 0.08), "USDCNH": rng.gauss(0.0, 0.005)}
                level["DGS10"] += ten / 100.0
                level["DGS2"] += two / 100.0
                for name, change in logs.items():
                    level[name] *= math.exp(change)
                value = (self.TRUE["DGS10"] * ten + self.TRUE["DGS10-DGS2"] * (ten - two)
                         + sum(self.TRUE[name] * change for name, change in logs.items()) + rng.gauss(0.0, 0.0005))
            returns[day] = value
            for name in level:
                levels[name][day] = level[name]
        for name in drop:
            levels.pop(name)
        return {"as_of": AS_OF, "returns": returns, "levels": levels,
                "units": {name: "percent" for name in levels}, "sources": {name: "fred" for name in levels}}

    def test_known_sensitivities_are_recovered_and_the_shocks_follow(self):
        inputs = self._inputs()
        payload = qs.build_macro(inputs)
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        fridays = [d for d in inputs["returns"] if date.fromisoformat(d).weekday() == 4]
        self.assertEqual(payload["inputs"]["observations"], len(fridays) - 1)  # changes between the weeks' last days
        self.assertEqual(payload["inputs"]["window"], [fridays[1], fridays[-1]])
        self.assertEqual(payload["inputs"]["factors"], list(qs.MACRO_FACTORS))
        fitted = payload["results"]["macro_exposures"]
        betas = {row["factor"]: row["beta"] for row in fitted["exposures"]}
        for name, beta in self.TRUE.items():
            tolerance = 0.00003 if name.startswith("DGS10") else 0.03
            self.assertAlmostEqual(betas[name], beta, delta=tolerance, msg=name)
        self.assertGreater(fitted["r_squared"], 0.9)
        shocks = payload["results"]["macro_sensitivity"]
        self.assertEqual(set(shocks), set(qs.MACRO_SHOCKS))
        self.assertAlmostEqual(shocks["rates_up_100bp"]["impact"], betas["DGS10"] * 100.0, places=12)
        self.assertAlmostEqual(shocks["rates_up_100bp"]["impact"], -0.02, delta=0.003)
        self.assertAlmostEqual(shocks["curve_steeper_50bp"]["impact"], betas["DGS10-DGS2"] * 50.0, places=12)
        self.assertAlmostEqual(shocks["dollar_up_5pct"]["impact"], betas["DTWEXBGS"] * math.log(1.05), places=12)
        self.assertAlmostEqual(shocks["oil_down_20pct"]["impact"], betas["DCOILBRENTEU"] * math.log(0.8), places=12)
        risk_off = sum(betas[name] * (shock if name.startswith("DGS10") else math.log1p(shock))
                       for name, shock in qs.MACRO_SHOCKS["risk_off"].items())
        self.assertAlmostEqual(shocks["risk_off"]["impact"], risk_off, places=12)

    def test_without_the_vix_the_section_is_built_and_says_why(self):
        # The default: VIXCLS is Cboe copyright and stays out of silver until its licence is confirmed.
        payload = qs.build_macro(self._inputs(drop=("VIXCLS",)))
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        inputs = payload["inputs"]
        self.assertEqual(inputs["factors"], [name for name in qs.MACRO_FACTORS if name != "VIXCLS"])
        self.assertEqual(inputs["factors_missing"], ["VIXCLS"])
        self.assertEqual(inputs["factors_missing_reasons"], {"VIXCLS": (
            "Not in silver macro_series for the window. VIXCLS is Cboe copyright, so the macro series loader "
            "keeps it only when MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS.")})
        fitted = payload["results"]["macro_exposures"]
        betas = {row["factor"]: row["beta"] for row in fitted["exposures"]}
        self.assertNotIn("VIXCLS", betas)
        shocks = payload["results"]["macro_sensitivity"]
        # The VIX shock alone is left out with the reason; the others are as with the VIX.
        self.assertEqual(set(shocks), set(qs.MACRO_SHOCKS) - {"volatility_up_50pct"})
        self.assertEqual(inputs["shocks_left_out"], {"volatility_up_50pct": (
            "Not worked out: VIXCLS is not in the model. " + inputs["factors_missing_reasons"]["VIXCLS"])})
        self.assertTrue(all(result.get("available") is not False for result in shocks.values()))
        self.assertAlmostEqual(shocks["rates_up_100bp"]["impact"], betas["DGS10"] * 100.0, places=12)
        # The risk-off week on its four other parts, and it says the VIX is not in it.
        risk_off = sum(betas[name] * (shock if name.startswith("DGS10") else math.log1p(shock))
                       for name, shock in qs.MACRO_SHOCKS["risk_off"].items() if name != "VIXCLS")
        self.assertAlmostEqual(shocks["risk_off"]["impact"], risk_off, places=12)
        self.assertEqual(shocks["risk_off"]["factors_not_in_model"], ["VIXCLS"])
        self.assertEqual({row["factor"] for row in shocks["risk_off"]["factors"]},
                         {"DGS10", "DTWEXBGS", "DCOILBRENTEU", "USDCNH"})
        json.dumps(qs.finite_copy(payload)[0], allow_nan=False)

    def test_a_series_not_loaded_is_left_out_and_named(self):
        payload = qs.build_macro(self._inputs(drop=("VIXCLS", "DGS2")))
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(payload["inputs"]["factors_missing"], ["DGS10-DGS2", "VIXCLS"])
        self.assertEqual(payload["inputs"]["factors_missing_reasons"]["DGS10-DGS2"],
                         "Needs DGS10 and DGS2; DGS2 not in silver macro_series for the window.")
        self.assertNotIn("DGS10-DGS2", {row["factor"] for row in payload["results"]["macro_exposures"]["exposures"]})
        self.assertEqual(set(payload["inputs"]["shocks_left_out"]), {"curve_steeper_50bp", "volatility_up_50pct"})
        self.assertIn("DGS10-DGS2 is not in the model. Needs DGS10 and DGS2",
                      payload["inputs"]["shocks_left_out"]["curve_steeper_50bp"])
        only_brent = qs.build_macro(self._inputs(drop=("DGS10", "DGS2", "DTWEXBGS", "VIXCLS", "USDCNH")))
        self.assertTrue(only_brent["available"], only_brent["reason"])
        self.assertEqual(set(only_brent["results"]["macro_sensitivity"]), {"oil_down_20pct", "risk_off"})
        self.assertEqual(only_brent["inputs"]["factors_missing_reasons"]["DGS10"],
                         "Not in silver macro_series for the window.")

    def test_too_short_a_history_is_unavailable(self):
        inputs = self._inputs()
        short = {day: value for day, value in inputs["returns"].items() if day >= _shift(AS_OF, -200)}
        payload = qs.build_macro({**inputs, "returns": short})
        self.assertFalse(payload["available"])
        self.assertIn("60", payload["reason"])
        self.assertFalse(qs.build_macro({**inputs, "levels": {}})["available"])


def _crowding(covered, filers, stakes, not_covered=()):
    return {"as_of": AS_OF, "holdings_date": AS_OF, "no_holdings": None, "covered": list(covered),
            "not_covered": list(not_covered), "filers": list(filers), "stakes": list(stakes)}


def _filer(cik, name, rest=(), **extra):
    rest = [float(v) for v in rest]
    return {"filer_cik": cik, "filer_name": name, "period": "2026-06-30", "accession": f"{cik}-1",
            "filed_at": "2026-08-14", "positions": len(rest), "total_value": sum(rest), "rest_value": sum(rest),
            "rest_squares": sum(v * v for v in rest), "rest_positions": len(rest), **extra}


class CrowdingTests(unittest.TestCase):
    COVERED = [{"name": "AAPL", "conid": "265598", "weight": 0.3, "cusip": "037833100"},
               {"name": "MSFT", "conid": "272093", "weight": 0.2, "cusip": "594918104"}]

    def test_a_fund_with_the_portfolio_s_own_mix_has_similarity_one(self):
        filers = [_filer("0000000001", "Twin"), _filer("0000000002", "Elsewhere", rest=[5e6, 3e6]),
                  _filer("0000000003", "Half", rest=[1e6])]
        stakes = [{"filer_cik": "0000000001", "cusip": "037833100", "shares": 1.0, "value_usd": 3e6},
                  {"filer_cik": "0000000001", "cusip": "594918104", "shares": 1.0, "value_usd": 2e6},
                  {"filer_cik": "0000000003", "cusip": "037833100", "shares": 1.0, "value_usd": 1e6}]
        payload = qs.build_crowding(_crowding(self.COVERED, filers, stakes))
        self.assertTrue(payload["available"], payload["reason"])
        found = {row["fund"]: row for row in payload["results"]["ownership_overlap"]["most_similar_funds"]}
        self.assertAlmostEqual(found["Twin"]["similarity"], 1.0, places=12)
        self.assertAlmostEqual(found["Elsewhere"]["similarity"], 0.0, places=12)
        # Half: (0.5, 0, 0.5) against (0.6, 0.4, 0) by direction.
        self.assertAlmostEqual(found["Half"]["similarity"], 0.3 / math.sqrt(0.3 ** 2 + 0.2 ** 2) / math.sqrt(2),
                               places=12)
        self.assertEqual(payload["results"]["ownership_overlap"]["most_similar_funds"][0]["fund"], "Twin")
        self.assertAlmostEqual(payload["inputs"]["covered_weight"], 0.5)
        self.assertEqual(payload["inputs"]["securities"], {"037833100": "AAPL", "594918104": "MSFT"})

    def test_the_rest_of_a_filing_keeps_its_total_and_its_sum_of_squares(self):
        rng = random.Random(4)
        for count in (1, 2, 3, 10, 57, 400):
            values = [float(rng.randint(10_000, 2_000_000)) for _ in range(count)]
            with self.subTest(count=count):
                stand_ins = qs._rest_of_filing(sum(values), sum(v * v for v in values), len(values))
                self.assertLessEqual(len(stand_ins), count)
                self.assertTrue(all(v > 0 for v in stand_ins))
                self.assertAlmostEqual(math.fsum(stand_ins) / sum(values), 1.0, places=12)
                self.assertAlmostEqual(math.fsum(v * v for v in stand_ins) / sum(v * v for v in values), 1.0,
                                       places=9)
        self.assertEqual(qs._rest_of_filing(0.0, 0.0, 0), [])
        self.assertEqual(qs._rest_of_filing(5.0, 25.0, 1), [5.0])

    def test_the_reduced_filing_gives_the_full_filing_s_figures(self):
        rng = random.Random(6)
        weights = {"037833100": 0.3, "594918104": 0.2}
        full, filers, stakes = {}, [], []
        for index in range(6):
            cik = f"000000001{index}"
            rest = [float(rng.randint(10_000, 2_000_000)) for _ in range(rng.randint(5, 80))]
            ours = {cusip: float(rng.randint(10_000, 3_000_000)) for cusip in weights if rng.random() < 0.8}
            full[f"Fund {index}"] = {**ours, **{f"R{i:08d}": v for i, v in enumerate(rest)}}
            filers.append(_filer(cik, f"Fund {index}", rest=rest))
            stakes += [{"filer_cik": cik, "cusip": cusip, "shares": 1.0, "value_usd": v} for cusip, v in ours.items()]
        expected = ownership_overlap(weights, full, unit="value")
        covered = [{**row, "weight": weights[row["cusip"]]} for row in self.COVERED]
        found = qs.build_crowding(_crowding(covered, filers, stakes))["results"]["ownership_overlap"]
        self.assertEqual([r["fund"] for r in found["most_similar_funds"]],
                         [r["fund"] for r in expected["most_similar_funds"]])
        for got, want in zip(found["most_similar_funds"], expected["most_similar_funds"]):
            for key in ("similarity", "portfolio_weight_in_fund_names", "fund_weight_in_portfolio_names",
                        "reported_value"):
                self.assertAlmostEqual(got[key] / (want[key] or 1.0), 1.0 if want[key] else 0.0, places=9, msg=key)
        self.assertEqual(found["share_in_names_funds_hold"], expected["share_in_names_funds_hold"])
        self.assertEqual([(r["security"], r["funds_holding"]) for r in found["holdings"]],
                         [(r["security"], r["funds_holding"]) for r in expected["holdings"]])

    def test_two_filers_with_one_name_are_told_apart(self):
        filers = [_filer("0000000001", "Gamma Fund"), _filer("0000000002", "Gamma Fund"), _filer("0000000003", "Solo")]
        stakes = [{"filer_cik": cik, "cusip": "037833100", "shares": 1.0, "value_usd": 1e6}
                  for cik in ("0000000001", "0000000002", "0000000003")]
        found = qs.build_crowding(_crowding(self.COVERED, filers, stakes))
        funds = {row["fund"] for row in found["results"]["ownership_overlap"]["most_similar_funds"]}
        self.assertEqual(funds, {"Gamma Fund (0000000001)", "Gamma Fund (0000000002)", "Solo"})

    def test_only_shares_with_a_us_isin_are_covered(self):
        data = synthetic_data(holdings=4, days=320)
        prepared = qs.prepare_inputs(data)
        instruments = {"9001": {"isin": "US0378331005", "asset_category": "STK"},
                       "9002": {"isin": "HK0000069689", "asset_category": "STK"},
                       "9003": {"isin": "US5949181045", "asset_category": "OPT"},
                       "9004": {"isin": None, "asset_category": "STK"}}
        inputs = qs.prepare_phase3_inputs(data, prepared, {"crowding": {"instruments": instruments,
                                                                        "filings": {"filers": [], "stakes": []}}})
        crowding = inputs["crowding"]
        self.assertEqual([(row["conid"], row["cusip"]) for row in crowding["covered"]], [("9001", "037833100")])
        self.assertEqual({row["conid"]: row["reason"] for row in crowding["not_covered"]},
                         {"9002": "Not a US ISIN, so not in 13F filings.", "9003": "Only shares are in 13F filings.",
                          "9004": "No ISIN in silver instruments."})
        self.assertEqual(set(inputs), {"crowding"})
        payload = qs.build_crowding(crowding)
        self.assertFalse(payload["available"])  # no fund to compare with
        nobody = qs.build_crowding({**crowding, "covered": []})
        self.assertEqual(nobody["reason"], "No holding has a US ISIN, so none is in the 13F filings.")


# ---------------------------------------------------------------- preparing the inputs


class PrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_data(days=560)
        cls.prepared = qs.prepare_inputs(cls.data)
        cls.market = market_from_raw(synthetic_market(cls.data), cls.data)

    def test_only_the_sections_whose_rows_were_read(self):
        found = qs.prepare_phase3_inputs(self.data, self.prepared, {**self.market, "liquidity": None, "crowding": None})
        self.assertEqual(set(found), {"factor_attribution", "macro"})
        self.assertEqual(qs.prepare_phase3_inputs(self.data, self.prepared, {}), {})

    def test_the_holdings_and_returns_are_the_v1_sections(self):
        found = qs.prepare_phase3_inputs(self.data, self.prepared, self.market)
        holdings = self.prepared["holdings_risk"]
        self.assertEqual(found["liquidity"]["holdings_date"], holdings["holdings_date"])
        self.assertEqual([h["name"] for h in found["liquidity"]["holdings"]], [h["name"] for h in holdings["holdings"]])
        self.assertEqual(found["liquidity"]["returns"], self.prepared["tail"]["returns"])
        # Volumes from the 45 days to the holdings date only, and by holding name.
        for name, series in found["liquidity"]["volumes"].items():
            self.assertGreaterEqual(min(series), _shift(holdings["holdings_date"], -qs.VOLUME_WINDOW_DAYS))
        self.assertNotIn("SYN8", found["liquidity"]["volumes"])  # synthetic_market gives the last holding none
        quantity = {row["conid"]: float(row["quantity"]) for row in self.data["positions"]
                    if row["date"] == holdings["holdings_date"]}
        self.assertEqual({h["conid"]: h["quantity"] for h in found["liquidity"]["holdings"]}, quantity)
        self.assertEqual(found["factor_attribution"]["region"], "developed")
        self.assertGreaterEqual(min(found["factor_attribution"]["returns"]), _shift(AS_OF, -365))
        self.assertGreaterEqual(min(found["macro"]["returns"]), _shift(AS_OF, -365 * 3 - 7))
        json.dumps(found, allow_nan=False)

    def test_an_own_pipeline_and_a_missing_comparison_say_why(self):
        own = {"subject_id": "alpha-world-ms", "as_of": AS_OF, "unavailable": "Reported through its own pipeline."}
        found = qs.prepare_phase3_inputs(own, qs.prepare_inputs(own), {name: {} for name in qs.PHASE3_NAMES})
        for name in qs.PHASE3_NAMES:
            payload = qs.BUILDERS[name](found[name])
            self.assertEqual((payload["available"], payload["reason"]), (False, "Reported through its own pipeline."))
        bare = {**self.data, "comparison": []}
        found = qs.prepare_phase3_inputs(bare, qs.prepare_inputs(bare), self.market)
        for name in ("factor_attribution", "macro"):
            self.assertIn("No primary benchmark comparison", qs.BUILDERS[name](found[name])["reason"])

    def test_the_fingerprint_follows_the_silver_rows(self):
        found = qs.prepare_phase3_inputs(self.data, self.prepared, self.market)
        before = {name: qs.section_fingerprint(name, found[name]) for name in qs.PHASE3_NAMES}
        again = qs.prepare_phase3_inputs(self.data, self.prepared, self.market)
        self.assertEqual({name: qs.section_fingerprint(name, again[name]) for name in qs.PHASE3_NAMES}, before)
        changed = json.loads(json.dumps(self.market))
        conid = sorted(changed["liquidity"]["volumes"])[0]
        day = max(changed["liquidity"]["volumes"][conid]["volumes"])
        changed["liquidity"]["volumes"][conid]["volumes"][day] += 1.0
        changed["macro"]["series"]["DGS10"][max(changed["macro"]["series"]["DGS10"])] += 0.5
        after = qs.prepare_phase3_inputs(self.data, self.prepared, changed)
        self.assertNotEqual(qs.section_fingerprint("liquidity", after["liquidity"]), before["liquidity"])
        self.assertNotEqual(qs.section_fingerprint("macro", after["macro"]), before["macro"])
        self.assertEqual(qs.section_fingerprint("crowding", after["crowding"]), before["crowding"])

    def test_every_payload_is_json_with_finite_numbers_and_passes(self):
        found = qs.prepare_phase3_inputs(self.data, self.prepared, self.market)
        for name in qs.PHASE3_NAMES:
            with self.subTest(section=name):
                payload, replaced = qs.finite_copy(qs.BUILDERS[name](found[name]))
                self.assertEqual(replaced, 0)
                self.assertTrue(payload["available"], payload["reason"])
                self.assertEqual(qs.quality_status(payload, replaced), "pass")
                self.assertEqual(set(payload), {"section", "model_id", "model_version", "as_of", "available",
                                                "reason", "inputs", "results"})
                json.dumps(payload, allow_nan=False)
        crowding = qs.build_crowding(found["crowding"])
        best = crowding["results"]["ownership_overlap"]["most_similar_funds"][0]
        self.assertEqual(best["fund"], "Echo Management")  # it holds our US names in our proportions
        self.assertAlmostEqual(best["similarity"], 1.0, places=6)
        # The amendment counts, the quarter filed after the date does not, and both Gamma funds are there.
        periods = {row["fund"]: row["period"] for row in crowding["inputs"]["filers"]}
        self.assertEqual(periods["Beta Partners"], "2026-06-30")
        self.assertEqual(periods["Hotel Holdings"], "2026-09-30")
        self.assertEqual({row["fund"]: row["accession"] for row in crowding["inputs"]["filers"]}["Alpha Capital"],
                         "0000100001-26-000002")
        self.assertEqual(len(crowding["inputs"]["filers"]), len(FILERS))


# ---------------------------------------------------------------- the build: gating


class GatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_data(days=560)
        cls.market = market_from_raw(synthetic_market(cls.data), cls.data)

    def _build(self, environ, *, present=(), market=None, sections=None):
        stored = []
        connection = _Connection({
            "pg_catalog.pg_class": lambda params: [{"present": params[0] in present}],
            "SELECT section, source_data_sha256": [],
        })

        def store(connection, **kwargs):
            stored.append(kwargs)

        environ = {publish_gate.MODE_ENV: "off", **environ}
        with patch.object(qs, "load_inputs", return_value=self.data), \
                patch.object(qs, "store_section", side_effect=store), \
                patch.object(publish_gate, "before_commit"), \
                patch.object(qs, "load_phase3_inputs", wraps=qs.load_phase3_inputs) as loader:
            if market is not None:
                loader.side_effect = lambda connection, **kwargs: {
                    name: (market.get(name) if mi.SECTION_TABLES[name] in present else None)
                    for name in kwargs["sections"]}
            result = qs.build_quant_snapshot(connection, tenant_id="t", subject_id=ACCOUNT, as_of=AS_OF,
                                             sections=sections, environ=environ)
        return result, stored, connection, loader

    def test_unset_builds_the_v1_sections_and_reads_nothing_more(self):
        for environ in ({}, {mi.PHASE3_SETTING: ""}, {mi.PHASE3_SETTING: "liquidity=yes,macro"}):
            with self.subTest(environ=environ):
                result, stored, connection, loader = self._build(environ, present=tuple(mi.SECTION_TABLES.values()))
                self.assertEqual([row["payload"]["section"] for row in stored], list(qs.SECTION_NAMES))
                loader.assert_not_called()
                self.assertNotIn("pg_catalog", connection.sql())
                for table in mi.SECTION_TABLES.values():
                    self.assertNotIn(table, connection.sql())
                self.assertNotIn("market_load_ms", result)
                self.assertEqual(set(result["sections"]), set(qs.SECTION_NAMES))
                self.assertTrue(all("market_load_ms" not in row["timings"] for row in stored))

    def test_a_section_whose_table_is_missing_is_skipped(self):
        result, stored, connection, loader = self._build(ALL_ON)
        self.assertEqual([row["payload"]["section"] for row in stored], list(qs.SECTION_NAMES))
        for name, table in mi.SECTION_TABLES.items():
            self.assertEqual(result["sections"][name], {"status": "skipped", "reason": f"{table} is not loaded yet"})
        # Asked of the catalog only; no read of a table that is not there.
        self.assertEqual(sum("pg_catalog" in sql for sql, _ in connection.statements), 4)
        self.assertNotIn("FROM daily_volumes", connection.sql())
        self.assertEqual(result["errors"], {})

    def test_switched_on_with_its_table_a_section_is_built_and_stored(self):
        present = tuple(mi.SECTION_TABLES.values())
        result, stored, _, loader = self._build(ALL_ON, present=present, market=self.market)
        names = [row["payload"]["section"] for row in stored]
        self.assertEqual(names, list(qs.SECTION_NAMES) + list(qs.PHASE3_NAMES))
        prepared = qs.prepare_inputs(self.data)
        expected = qs.prepare_phase3_inputs(self.data, prepared, self.market)
        for row in stored[len(qs.SECTION_NAMES):]:
            name = row["payload"]["section"]
            with self.subTest(section=name):
                self.assertEqual(row["payload"], qs.finite_copy(qs.BUILDERS[name](expected[name]))[0])
                self.assertEqual(row["source_sha256"], qs.section_fingerprint(name, expected[name]))
                self.assertEqual(row["quality"], "pass")
                self.assertIn("market_load_ms", row["timings"])
                self.assertEqual(result["sections"][name]["status"], "rebuilt")
        self.assertTrue(all("market_load_ms" not in row["timings"] for row in stored[:len(qs.SECTION_NAMES)]))
        self.assertIn("market_load_ms", result)
        self.assertEqual(loader.call_args.kwargs["sections"], qs.PHASE3_NAMES)

    def test_each_section_follows_its_own_setting_and_table(self):
        environ = {mi.PHASE3_SETTING: "liquidity=shadow,macro=off,crowding=on"}
        result, stored, _, loader = self._build(environ, present=("daily_volumes",), market=self.market)
        self.assertEqual([row["payload"]["section"] for row in stored][len(qs.SECTION_NAMES):], ["liquidity"])
        self.assertEqual(result["sections"]["crowding"]["status"], "skipped")
        self.assertNotIn("macro", result["sections"])
        self.assertEqual(loader.call_args.kwargs["sections"], ("liquidity", "crowding"))

    def test_the_sections_argument_can_name_phase_3_sections(self):
        result, stored, _, _ = self._build(ALL_ON, present=("institutional_holdings",), market=self.market,
                                           sections=["tail", "crowding"])
        self.assertEqual([row["payload"]["section"] for row in stored], ["tail", "crowding"])
        result, stored, _, loader = self._build({}, sections=["liquidity"])  # switched off: nothing
        self.assertEqual((stored, result["written"], result["status"]), ([], 0, "unchanged"))
        loader.assert_not_called()
        with self.assertRaises(ValueError):
            self._build(ALL_ON, sections=["fair_value"])

    def test_the_loader_reads_each_table_for_the_latest_holdings(self):
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}]})
        instruments = {conid: {"symbol": f"SYN{conid[-1]}", "listing_exchange": None,
                               "currency": "HKD" if conid == "9003" else "USD",
                               "isin": "US0378331005" if conid == "9001" else None, "asset_category": "STK"}
                       for conid in [str(9001 + i) for i in range(8)]}
        with patch.object(mi, "read_instruments", return_value=instruments) as read_instruments, \
                patch.object(mi, "read_daily_volumes", return_value={}) as volumes, \
                patch.object(mi, "read_factor_returns", return_value={"factors": {}}) as factors, \
                patch.object(mi, "read_macro_series", return_value={"series": {}}) as macro, \
                patch.object(mi, "read_institutional_holdings", return_value={"filers": [], "stakes": []}) as filings:
            found = qs.load_phase3_inputs(connection, tenant_id="t", subject_id=ACCOUNT, as_of=AS_OF, data=self.data,
                                          sections=qs.PHASE3_NAMES)
        self.assertEqual(found["liquidity"], {"volumes": {}})
        self.assertEqual(found["factor_attribution"], {"factors": {}})
        self.assertEqual(found["crowding"]["filings"], {"filers": [], "stakes": []})
        self.assertEqual(found["crowding"]["instruments"]["9001"], {"isin": "US0378331005", "asset_category": "STK"})
        self.assertEqual(read_instruments.call_args.args[1], sorted(instruments))
        self.assertEqual(volumes.call_args.kwargs["instrument_ids"], sorted(instruments))  # the held conids
        self.assertEqual((volumes.call_args.kwargs["start"], volumes.call_args.kwargs["end"]),
                         (_shift(AS_OF, -qs.VOLUME_WINDOW_DAYS), AS_OF))
        self.assertEqual(factors.call_args.kwargs, {"tenant_id": "t", "region": "developed",
                                                    "start": _shift(AS_OF, -365), "end": AS_OF})
        self.assertEqual(macro.call_args.kwargs["start"], _shift(AS_OF, -365 * 3 - 7))
        self.assertEqual(filings.call_args.kwargs, {"tenant_id": "t", "as_of": AS_OF, "cusips": ["037833100"]})
        # A table not there yet is None, and nothing else is read for it.
        connection = _Connection({"pg_catalog.pg_class": [{"present": False}]})
        self.assertEqual(qs.load_phase3_inputs(connection, tenant_id="t", subject_id=ACCOUNT, as_of=AS_OF,
                                               data=self.data, sections=qs.PHASE3_NAMES),
                         {name: None for name in qs.PHASE3_NAMES})
        self.assertEqual(len(connection.statements), 4)
        # An own-pipeline subject reads nothing but the catalog.
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}]})
        own = {"subject_id": "alpha-world-ms", "as_of": AS_OF, "unavailable": "own pipeline"}
        self.assertEqual(qs.load_phase3_inputs(connection, tenant_id="t", subject_id="alpha-world-ms", as_of=AS_OF,
                                               data=own, sections=["macro"]), {"macro": {}})
        self.assertEqual(len(connection.statements), 1)

    def test_each_table_read_is_an_input_of_the_job_s_lineage_run(self):
        from ibkr_etl import lineage

        record = lineage.Run("ibkrv2stg-quant-analytics")
        record.active = True
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}]})
        with patch.object(lineage, "_CURRENT", [record]), \
                patch.object(mi, "read_instruments", return_value={}), \
                patch.object(mi, "read_daily_volumes", return_value={}), \
                patch.object(mi, "read_factor_returns", return_value={"factors": {}}), \
                patch.object(mi, "read_macro_series", return_value=None), \
                patch.object(mi, "read_institutional_holdings", return_value={"filers": [], "stakes": []}):
            for _ in range(2):  # a second subject adds nothing new
                qs.load_phase3_inputs(connection, tenant_id="t", subject_id=ACCOUNT, as_of=AS_OF, data=self.data,
                                      sections=qs.PHASE3_NAMES)
        inputs = sorted(name for direction, _, name in record._datasets if direction == "input")
        # macro_series was not there to read (None), so it is not an input.
        self.assertEqual(inputs, ["silver.daily_volumes", "silver.factor_returns", "silver.institutional_holdings"])


if __name__ == "__main__":
    unittest.main()
