"""Crisis prices in silver (CRISIS_PRICES_SOURCE): made-up prices and holdings only."""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from datetime import date, timedelta
from unittest import mock

from ibkr_etl import crisis_prices as cp
from ibkr_etl import crisis_scenarios as cs
from ibkr_etl.crisis_replay import CRISIS_END, CRISIS_START, refresh_composite_crisis_replay, refresh_crisis_replay

import test_crisis_replay_krw as krw
import test_crisis_scenarios as scenarios

MARKET_TABLE = """
    CREATE TABLE market_data_prices (instrument_id TEXT, price_date TEXT, symbol TEXT, instrument_name TEXT,
        price_type TEXT, currency TEXT, price REAL, source TEXT, source_url TEXT, fetched_at TEXT,
        PRIMARY KEY (instrument_id, price_date, price_type));
"""
# As the replay's own writer expects it (its ON CONFLICT names tenant_id).
TENANT_MARKET_TABLE = """
    CREATE TABLE market_data_prices (tenant_id TEXT DEFAULT 't', instrument_id TEXT, price_date TEXT,
        symbol TEXT, instrument_name TEXT, price_type TEXT, currency TEXT, price REAL, source TEXT,
        source_url TEXT, fetched_at TEXT, UNIQUE (tenant_id, instrument_id, price_date, price_type));
"""


class _Frame:
    def __init__(self, closes: dict[str, float]):
        self.closes = closes

    def iterrows(self):
        for day, value in sorted(self.closes.items()):
            yield day, {"Adj Close": value, "Close": value}


class Yahoo:
    """A made-up Yahoo: ticker -> {date: close}. down makes every request fail as a network error would."""

    def __init__(self, series: dict[str, dict[str, float]], *, down: bool = False):
        self.series, self.down, self.calls = series, down, []

    def __call__(self, symbol: str):
        outer = self

        class _Ticker:
            def history(self, *, start, end, **_):
                outer.calls.append((symbol, start, end))
                if outer.down:
                    raise ConnectionError("Yahoo is not answering")
                return _Frame({d: v for d, v in outer.series.get(symbol, {}).items() if start <= d < end})
        return _Ticker()


def weekdays(start: date, end: date) -> list[str]:
    return [d.isoformat() for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
            if d.weekday() < 5]


def silver_mode():
    return mock.patch.dict(os.environ, {cp.SOURCE_ENV: "silver"})


def stored(connection, table: str, columns: str, order: str) -> list[tuple]:
    return [tuple(r) for r in connection.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")]


class ModeTests(unittest.TestCase):
    def test_off_unless_set_to_silver_and_nothing_else_is_accepted(self):
        self.assertEqual(cp.mode({}), "off")
        self.assertEqual(cp.mode({cp.SOURCE_ENV: " "}), "off")
        self.assertEqual(cp.mode({cp.SOURCE_ENV: " Silver "}), "silver")
        with self.assertRaisesRegex(RuntimeError, "must be off or silver"):
            cp.mode({cp.SOURCE_ENV: "on"})


class StoreAndReadTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MARKET_TABLE)
        self.start, self.end = date(2020, 2, 19), date(2020, 3, 23)
        self.days = weekdays(self.start, self.end)
        self.closes = {d: 100.0 - i for i, d in enumerate(self.days)}

    def fetch(self, closes, symbol="XLK"):
        fetched = cp.Yahoo(Yahoo({symbol: closes}), keep=True)
        fetched.adjusted(symbol, self.start, self.end, "USD")
        return fetched, cp.write(self.connection, fetched, fetched_at="2026-10-24T00:00:00+00:00")

    def test_a_reply_is_stored_and_read_back_exactly(self):
        fetched, written = self.fetch(self.closes)
        self.assertEqual(written, {"answers": 1, "inserted": len(self.days)})
        silver = cp.Silver(self.connection, fetched)
        self.assertEqual(silver.adjusted("XLK", self.start, self.end), self.closes)
        self.assertEqual(dict(silver.counts), {"reads": 1, "same_as_fetch": 1})
        row = self.connection.execute("SELECT * FROM market_data_prices LIMIT 1").fetchone()
        self.assertEqual((row["instrument_id"], row["symbol"], row["price_type"], row["currency"]),
                         ("yahoo-xlk", "XLK", "adjusted_close", "USD"))
        self.assertEqual(row["instrument_name"], "Technology Select Sector SPDR Fund")

    def test_a_new_reply_replaces_the_dates_it_asked_for_and_nothing_else(self):
        self.fetch(self.closes)
        self.connection.execute("INSERT INTO market_data_prices VALUES ('yahoo-xlk', '2021-01-04', 'XLK', 'x', "
                                "'adjusted_close', 'USD', 5.0, 's', 'u', 't')")
        changed = {d: v for d, v in self.closes.items() if d != self.days[3]}
        changed[self.days[0]] = 1.5
        _, written = self.fetch(changed)
        self.assertEqual(written, {"answers": 1, "deleted": 1, "updated": 1, "unchanged": len(self.days) - 2})
        self.assertEqual(cp.Silver(self.connection, cp.Yahoo(keep=True)).adjusted("XLK", self.start, self.end),
                         changed)
        self.assertEqual(self.connection.execute(
            "SELECT price FROM market_data_prices WHERE price_date = '2021-01-04'").fetchone()[0], 5.0)

    def test_an_empty_or_failed_reply_keeps_the_last_good_answer(self):
        self.fetch(self.closes)
        fetched, written = self.fetch({})
        self.assertEqual(written, {"empty_answers": 1})
        down = cp.Yahoo(Yahoo({}, down=True), keep=True)
        with self.assertRaises(ConnectionError):
            down.adjusted("XLK", self.start, self.end)
        self.assertEqual(cp.write(self.connection, down), {})
        self.assertEqual(cp.Silver(self.connection, down).adjusted("XLK", self.start, self.end), self.closes)

    def test_nothing_in_silver_is_empty_only_when_todays_fetch_answered(self):
        fetched, _ = self.fetch({}, symbol="NOPE")
        silver = cp.Silver(self.connection, fetched)
        self.assertEqual(silver.adjusted("NOPE", self.start, self.end), {})
        with self.assertRaises(cp.NotInSilver):
            silver.adjusted("NEVER", self.start, self.end)
        down = cp.Yahoo(Yahoo({}, down=True), keep=True)
        with self.assertRaises(ConnectionError):
            down.adjusted("GONE", self.start, self.end)
        with self.assertRaises(cp.NotInSilver):
            cp.Silver(self.connection, down).adjusted("GONE", self.start, self.end)
        self.assertEqual(cp.Silver(self.connection, down, raised_is_empty=True).adjusted("GONE", self.start, self.end),
                         {})

    def test_fx_read_from_silver_equals_fx_from_yahoo_in_every_direction(self):
        quotes = {"HKD=X": {d: 7.75 + i / 1000 for i, d in enumerate(self.days)},
                  "TWD=X": {d: 31.0 + i / 100 for i, d in enumerate(self.days) if i != 4}}
        fetched = cp.Yahoo(Yahoo(quotes), keep=True)
        pairs = (("HKD", "USD"), ("USD", "HKD"), ("TWD", "HKD"))
        expected = {pair: fetched.fx(*pair, self.start, self.end) for pair in pairs}
        cp.write(self.connection, fetched)
        silver = cp.Silver(self.connection, fetched)
        for pair in pairs:
            self.assertEqual(silver.fx(*pair, self.start, self.end), expected[pair], pair)
        rows = {(r["symbol"], r["price_type"], r["currency"]) for r in self.connection.execute(
            "SELECT DISTINCT symbol, price_type, currency FROM market_data_prices")}
        self.assertEqual(rows, {("HKD=X", "unadjusted_close", "HKD"), ("TWD=X", "unadjusted_close", "TWD")})
        with self.assertRaisesRegex(RuntimeError, "No approved provisional FX ticker"):
            silver.fx("EUR", "USD", self.start, self.end)

    def test_an_fx_quote_yahoo_has_no_close_for_reads_back_as_no_close(self):
        fetched = cp.Yahoo(Yahoo({}), keep=True)
        with self.assertRaises(RuntimeError):
            fetched.fx("HKD", "USD", self.start, self.end)
        silver = cp.Silver(self.connection, fetched)
        with self.assertRaisesRegex(RuntimeError, "observations"):
            silver.fx("HKD", "USD", self.start, self.end)
        with self.assertRaises(cp.NotInSilver):
            silver.fx("KRW", "USD", self.start, self.end)


SCENARIO_MARKET = {**{k: v for k, v in scenarios.SERIES.items()},
                   "HKD=X": {d: 7.8 for d in scenarios.SERIES["SPY"]}}


def scenario_database() -> sqlite3.Connection:
    connection = scenarios.database()
    connection.executescript(MARKET_TABLE)
    return connection


def scenario_results(connection) -> dict:
    out = {}
    for r in connection.execute("SELECT subject_id, scenario_id, result_json FROM crisis_scenario_results"):
        out[(r["subject_id"], r["scenario_id"])] = json.loads(r["result_json"])
    return out


class CrisisScenarioTests(unittest.TestCase):
    def refresh(self, connection, market, **kw):
        return cs.refresh(connection, accounts=["U0000001"], composites=["demo-composite"],
                          ticker_factory=market, **kw)

    def test_silver_gives_the_same_results_as_fetching_while_computing(self):
        off = scenario_database()
        self.refresh(off, Yahoo(SCENARIO_MARKET), source="off")
        on = scenario_database()
        with mock.patch("builtins.print") as printed:
            summary = self.refresh(on, Yahoo(SCENARIO_MARKET), source="silver")
        self.assertEqual(scenario_results(on), scenario_results(off))
        self.assertTrue(any(r["available"] for r in scenario_results(on).values()))
        self.assertEqual(summary["failures"], {})
        reads = summary["prices"]["reads"]
        self.assertNotIn("differs_from_fetch", reads)
        self.assertNotIn("not_in_silver", reads)
        self.assertGreater(summary["prices"]["written"]["inserted"], 0)
        line = printed.call_args_list[-1].args[0]
        self.assertTrue(line.startswith(cp.LOG_PREFIX))
        self.assertNotIn("U0000001", line)
        self.assertNotIn("OLD", line)
        self.assertEqual(stored(off, "market_data_prices", "COUNT(*)", "1"), [(0,)])

    def test_with_yahoo_down_silver_keeps_yesterdays_prices(self):
        connection = scenario_database()
        connection.execute("DELETE FROM position_snapshots WHERE conid = '2'")  # every holding has prices
        with mock.patch("builtins.print"):
            self.refresh(connection, Yahoo(SCENARIO_MARKET), source="silver")
            first = scenario_results(connection)
            connection.execute("DELETE FROM crisis_scenario_results")
            summary = self.refresh(connection, Yahoo({}, down=True), source="silver")
        self.assertEqual(scenario_results(connection), first)
        self.assertEqual(summary["failures"], {})
        self.assertGreater(summary["prices"]["failed_requests"], 0)
        self.assertGreater(summary["prices"]["reads"]["from_an_earlier_fetch"], 0)

    def test_with_yahoo_down_a_ticker_yahoo_has_no_prices_for_stops_its_subject(self):
        # NEW listed in 2024: Yahoo's "no prices" for 2008 is not kept in silver, so on a day Yahoo
        # cannot be asked the account stops and keeps its last stored results.
        connection = scenario_database()
        with mock.patch("builtins.print"):
            self.refresh(connection, Yahoo(SCENARIO_MARKET), source="silver")
            connection.execute("DELETE FROM crisis_scenario_results")
            summary = self.refresh(connection, Yahoo({}, down=True), source="silver")
        self.assertIn("U0000001", summary["failures"])
        self.assertIn("silver.market_data_prices has no", summary["failures"]["U0000001"])
        self.assertNotIn("U0000001", {subject for subject, _ in scenario_results(connection)})

    def test_the_calculation_from_silver_never_calls_yahoo(self):
        connection, market, original, steps = scenario_database(), Yahoo(SCENARIO_MARKET), cs._walk, []

        def counting(*args):
            before = len(market.calls)
            out = original(*args)
            steps.append((type(args[3].source).__name__, args[4] is None, len(market.calls) - before))
            return out
        with mock.patch("builtins.print"), mock.patch.object(cs, "_walk", side_effect=counting):
            self.refresh(connection, market, source="silver")
        self.assertEqual(steps[1], ("Silver", False, 0))
        self.assertEqual(steps[0][:2], ("Yahoo", True))
        self.assertGreater(steps[0][2], 0)

    def test_a_price_silver_does_not_hold_stops_the_subject(self):
        connection = scenario_database()
        with self.assertRaises(cp.NotInSilver):
            cs.account_scenarios(connection, "U0000001", cs.Prices(source=cp.Silver(connection)))


def usd_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(TENANT_MARKET_TABLE + """
        CREATE TABLE nav_snapshots (account_id TEXT, snapshot_date TEXT, asset_class TEXT, total_text TEXT);
        CREATE TABLE position_snapshots (account_id TEXT, snapshot_date TEXT, conid TEXT, currency TEXT,
                                         market_value_text TEXT);
        CREATE TABLE instruments (conid TEXT, symbol TEXT, description TEXT, listing_exchange TEXT);
        CREATE TABLE reference_fx_rates (from_currency TEXT, to_currency TEXT, rate_date TEXT, rate REAL,
                                         quality_status TEXT);
        CREATE TABLE crisis_replay_components (subject_id TEXT, holdings_as_of TEXT, conid TEXT,
            holding_symbol TEXT, description TEXT, currency TEXT, current_market_value REAL,
            opening_weight REAL, source_symbol TEXT, mapping_method TEXT, proxy_reason TEXT, price_type TEXT,
            provider TEXT, fetched_at TEXT);
        CREATE TABLE crisis_replay_daily (subject_id TEXT, holdings_as_of TEXT, observation_date TEXT,
            normalized_value REAL, drawdown REAL, crisis_start TEXT, crisis_end TEXT, calculation_version TEXT,
            quality_status TEXT, generated_at TEXT);
        INSERT INTO nav_snapshots VALUES ('U0000002', '2026-09-30', 'TOTAL', '1000');
        INSERT INTO position_snapshots VALUES ('U0000002', '2026-09-30', 'A', 'USD', '300'),
            ('U0000002', '2026-09-30', 'B', 'USD', '200'), ('U0000002', '2026-09-30', 'C', 'HKD', '1560');
        INSERT INTO instruments VALUES ('A', 'OLD', 'Old Co', 'NYSE'), ('B', 'ANET', 'Arista', 'NYSE'),
            ('C', '5', 'HSBC', 'SEHK');
        INSERT INTO reference_fx_rates VALUES ('HKD', 'USD', '2026-09-30', 0.128, 'pass');
    """)
    return connection


SPY_DAYS = weekdays(CRISIS_START, CRISIS_END)
LOOKBACK_DAYS = weekdays(CRISIS_START - timedelta(days=10), CRISIS_END)
USD_MARKET = {symbol: {d: 100.0 - i * step for i, d in enumerate(LOOKBACK_DAYS)} for symbol, step in (
    ("SPY", 0.2), ("OLD", 0.3), ("XLK", 0.25), ("XLI", 0.1), ("XLV", 0.1), ("XLY", 0.1), ("XLB", 0.1),
    ("EWT", 0.1), ("EWH", 0.15), ("FXI", 0.1), ("0005.HK", 0.12), ("^HSI", 0.18))}
USD_MARKET["SPY"] = {d: v for d, v in USD_MARKET["SPY"].items() if d >= CRISIS_START.isoformat()}
USD_MARKET["HKD=X"] = {d: 7.75 + i / 10000 for i, d in enumerate(LOOKBACK_DAYS)}


def replay_rows(connection):
    return (stored(connection, "crisis_replay_daily", "subject_id, observation_date, normalized_value, drawdown",
                   "subject_id, observation_date"),
            stored(connection, "crisis_replay_components",
                   "subject_id, conid, source_symbol, mapping_method, opening_weight", "subject_id, conid"),
            stored(connection, "market_data_prices", "instrument_id, price_date, price, currency",
                   "instrument_id, price_date"))


class CrisisReplayTests(unittest.TestCase):
    def database(self):
        return usd_database()

    def test_a_usd_replay_from_silver_equals_one_fetched_while_computing(self):
        off = self.database()
        result_off = refresh_crisis_replay(off, account_id="U0000002", ticker_factory=Yahoo(USD_MARKET))
        on = self.database()
        with silver_mode(), mock.patch("builtins.print"):
            result_on = refresh_crisis_replay(on, account_id="U0000002", ticker_factory=Yahoo(USD_MARKET))
        self.assertEqual(result_on, result_off)
        self.assertEqual(result_on["direct_components"], 2)   # OLD and the HSBC listing; ANET takes XLK
        daily_on, components_on, prices_on = replay_rows(on)
        daily_off, components_off, prices_off = replay_rows(off)
        self.assertEqual((daily_on, components_on), (daily_off, components_off))
        replay_prices = [r for r in prices_on if r[0].startswith("crisis-replay-")]
        self.assertEqual(replay_prices, prices_off)
        source = {r[0] for r in prices_on if r[0].startswith("yahoo-")}
        self.assertTrue({"yahoo-spy", "yahoo-xlk", "yahoo-0005.hk", "yahoo-hkd=x"} <= source)

    def test_with_yahoo_down_the_replay_is_built_from_silver(self):
        connection = self.database()
        connection.execute("DELETE FROM position_snapshots WHERE conid = 'B'")  # every holding has prices
        with silver_mode(), mock.patch("builtins.print"):
            first = refresh_crisis_replay(connection, account_id="U0000002", ticker_factory=Yahoo(USD_MARKET))
            daily = replay_rows(connection)[0]
            again = refresh_crisis_replay(connection, account_id="U0000002",
                                          ticker_factory=Yahoo({}, down=True))
        self.assertEqual(again, first)
        self.assertEqual(replay_rows(connection)[0], daily)

    def test_with_yahoo_down_and_nothing_in_silver_the_replay_stops(self):
        connection = self.database()
        with silver_mode(), mock.patch("builtins.print"), self.assertRaises(cp.NotInSilver):
            refresh_crisis_replay(connection, account_id="U0000002", ticker_factory=Yahoo({}, down=True))
        self.assertEqual(stored(connection, "crisis_replay_daily", "COUNT(*)", "1"), [(0,)])
        # As when fetching while computing: a ticker with no 2008 prices (ANET) needs today's answer.
        with silver_mode(), mock.patch("builtins.print"):
            refresh_crisis_replay(connection, account_id="U0000002", ticker_factory=Yahoo(USD_MARKET))
        with silver_mode(), mock.patch("builtins.print"), self.assertRaisesRegex(cp.NotInSilver, "ANET"):
            refresh_crisis_replay(connection, account_id="U0000002", ticker_factory=Yahoo({}, down=True))

    def test_a_shared_price_cache_is_not_fetched_again_and_holds_only_silver_reads(self):
        def twice(connection, market, cache):
            return [refresh_crisis_replay(connection, account_id="U0000002", ticker_factory=market,
                                          price_cache=cache) for _ in range(2)]

        off_market, off_cache = Yahoo(USD_MARKET), {}
        off = twice(self.database(), off_market, off_cache)
        on_market, on_cache = Yahoo(USD_MARKET), {}
        with silver_mode(), mock.patch("builtins.print"):
            on = twice(self.database(), on_market, on_cache)
        self.assertEqual(on, off)
        self.assertEqual(on_cache, off_cache)
        # The second fetch asks only for what the cache does not hold, as the second calculation does.
        self.assertEqual(sorted(on_market.calls), sorted(off_market.calls))

    def test_a_won_replay_from_silver_equals_one_fetched_while_computing(self):
        off = krw.database()
        result_off = refresh_crisis_replay(off, account_id="KIWOOM-1", reporting_currency="KRW",
                                           ticker_factory=lambda s: krw._Ticker(krw.MARKET.get(s, {})))
        on = krw.database()
        with silver_mode(), mock.patch("builtins.print"):
            result_on = refresh_crisis_replay(on, account_id="KIWOOM-1", reporting_currency="KRW",
                                              ticker_factory=Yahoo(krw.MARKET))
        self.assertEqual(result_on, result_off)
        self.assertEqual(replay_rows(on)[:2], replay_rows(off)[:2])

    def test_a_composite_hedge_replay_from_silver_equals_one_fetched_while_computing(self):
        def database():
            connection = self.database()
            connection.executescript("""
                CREATE TABLE subject_nav_series (subject_id TEXT, snapshot_date TEXT, total_text TEXT);
                CREATE TABLE synthetic_portfolio_components (portfolio_id TEXT, snapshot_date TEXT,
                    component_id TEXT, component_name TEXT, component_type TEXT, direction TEXT,
                    contribution REAL, source_currency TEXT);
                INSERT INTO subject_nav_series VALUES ('demo-hedged', '2026-09-30', '1000');
                INSERT INTO synthetic_portfolio_components VALUES
                    ('demo-hedged', '2026-09-30', 'hang-seng-price-index', 'Hang Seng', 'index_hedge', 'short',
                     -200, 'HKD'),
                    ('demo-hedged', '2026-09-30', 'spy-settlement', 'SPY', 'equity', 'long', 500, 'USD');
            """)
            return connection

        def run(connection, market):
            with mock.patch("ibkr_etl.crisis_replay.resolve_holdings_as_of", return_value="2026-09-30"), \
                    mock.patch("ibkr_etl.crisis_replay.load_crisis_replay", return_value={"loaded": True}):
                return refresh_composite_crisis_replay(connection, account_id="demo-hedged", ticker_factory=market)

        off, on = database(), database()
        run(off, Yahoo(USD_MARKET))
        with silver_mode(), mock.patch("builtins.print"):
            run(on, Yahoo(USD_MARKET))
            self.assertEqual(replay_rows(on)[:2], replay_rows(off)[:2])
            run(on, Yahoo({}, down=True))
        self.assertEqual(replay_rows(on)[:2], replay_rows(off)[:2])
        self.assertGreater(len(replay_rows(on)[0]), 100)


if __name__ == "__main__":
    unittest.main()
