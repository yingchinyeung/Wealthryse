"""A Korean-won account's crisis replay: KRX prices in won, on the SPY window, with KOSPI/KOSDAQ proxies."""
from __future__ import annotations

import sqlite3
import unittest
from datetime import date, timedelta

from ibkr_etl.crisis_replay import CRISIS_END, CRISIS_START, refresh_crisis_replay

CHUSEOK = "2008-09-15"  # KRX was closed on the window's first day


def weekdays(start: date, end: date, skip: tuple[str, ...] = ()) -> list[str]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in skip:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


class _Frame:
    def __init__(self, closes: dict[str, float]):
        self.closes = closes

    def iterrows(self):
        for day, value in self.closes.items():
            yield day, {"Adj Close": value}


class _Ticker:
    def __init__(self, series: dict[str, float]):
        self.series = series

    def history(self, *, start, end, **_):
        return _Frame({d: v for d, v in self.series.items() if start <= d < end})


def falling(days: list[str], first: float, last: float) -> dict[str, float]:
    step = (last - first) / max(1, len(days) - 1)
    return {day: first + step * i for i, day in enumerate(days)}


LOOKBACK = CRISIS_START - timedelta(days=10)
KRX = weekdays(LOOKBACK, CRISIS_END, skip=(CHUSEOK,))
MARKET = {
    "SPY": falling(weekdays(CRISIS_START, CRISIS_END), 120.0, 70.0),
    "^KS11": falling(KRX, 1500.0, 1000.0),
    "^KQ11": falling(KRX, 500.0, 300.0),
    "005930.KS": falling(KRX, 100.0, 50.0),           # complete: replayed directly
    "123456.KQ": falling(KRX[40:], 10.0, 9.0),         # listed after the crisis began: KOSDAQ proxy
}


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE nav_snapshots (account_id TEXT, snapshot_date TEXT, asset_class TEXT, total_text TEXT);
        CREATE TABLE position_snapshots (account_id TEXT, snapshot_date TEXT, conid TEXT, currency TEXT,
                                         market_value_text TEXT);
        CREATE TABLE instruments (conid TEXT, symbol TEXT, description TEXT, listing_exchange TEXT);
        CREATE TABLE market_data_prices (tenant_id TEXT DEFAULT 't', instrument_id TEXT, price_date TEXT,
            symbol TEXT, instrument_name TEXT, price_type TEXT, currency TEXT, price REAL, source TEXT,
            source_url TEXT, fetched_at TEXT, UNIQUE (tenant_id, instrument_id, price_date, price_type));
        CREATE TABLE crisis_replay_components (subject_id TEXT, holdings_as_of TEXT, conid TEXT,
            holding_symbol TEXT, description TEXT, currency TEXT, current_market_value REAL,
            opening_weight REAL, source_symbol TEXT, mapping_method TEXT, proxy_reason TEXT, price_type TEXT,
            provider TEXT, fetched_at TEXT);
        CREATE TABLE crisis_replay_daily (subject_id TEXT, holdings_as_of TEXT, observation_date TEXT,
            normalized_value REAL, drawdown REAL, crisis_start TEXT, crisis_end TEXT, calculation_version TEXT,
            quality_status TEXT, generated_at TEXT);
    """)
    connection.execute("INSERT INTO nav_snapshots VALUES ('KIWOOM-1', '2026-09-30', 'TOTAL', '1000000')")
    for conid, symbol, value in (("K1", "005930", "500000"), ("K2", "123456", "200000"), ("K3", "999999", "100000")):
        connection.execute("INSERT INTO position_snapshots VALUES ('KIWOOM-1', '2026-09-30', ?, 'KRW', ?)",
                           (conid, value))
        connection.execute("INSERT INTO instruments VALUES (?, ?, ?, 'KRX')", (conid, symbol, symbol))
    return connection


class KrwCrisisReplayTests(unittest.TestCase):
    def test_a_won_account_replays_krx_prices_in_won_with_index_proxies(self):
        connection = database()
        result = refresh_crisis_replay(connection, account_id="KIWOOM-1", reporting_currency="KRW",
                                       ticker_factory=lambda symbol: _Ticker(MARKET.get(symbol, {})))
        self.assertEqual(result["reporting_currency"], "KRW")
        self.assertEqual((result["direct_components"], result["proxy_components"]), (1, 2))
        rows = {r["holding_symbol"]: (r["source_symbol"], r["mapping_method"], r["currency"]) for r in
                connection.execute("SELECT * FROM crisis_replay_components")}
        self.assertEqual(rows, {"005930": ("005930.KS", "direct", "KRW"),
                                "123456": ("^KQ11", "market_proxy", "KRW"),
                                "999999": ("^KS11", "market_proxy", "KRW")})
        days = [r["observation_date"] for r in connection.execute(
            "SELECT observation_date FROM crisis_replay_daily ORDER BY observation_date")]
        self.assertEqual((days[0], days[-1]), (CRISIS_START.isoformat(), CRISIS_END.isoformat()))
        # 50%, 20% and 10% move from the last close before the window (12 Sep) to its last
        # KRX close; 20% is flat cash.
        first, last = "2008-09-12", max(d for d in KRX if d <= CRISIS_END.isoformat())

        def move(symbol: str) -> float:
            return MARKET[symbol][last] / MARKET[symbol][first]

        expected = 0.5 * move("005930.KS") + 0.2 * move("^KQ11") + 0.1 * move("^KS11") + 0.2
        self.assertAlmostEqual(result["maximum_drawdown"], expected - 1.0, places=2)
        currencies = {r["currency"] for r in connection.execute("SELECT currency FROM market_data_prices")}
        self.assertEqual(currencies, {"KRW"})

    def test_the_chuseok_opening_uses_the_last_close_before_it(self):
        connection = database()
        refresh_crisis_replay(connection, account_id="KIWOOM-1", reporting_currency="KRW",
                              ticker_factory=lambda symbol: _Ticker(MARKET.get(symbol, {})))
        first = connection.execute("SELECT price FROM market_data_prices WHERE symbol = '005930.KS' "
                                   "AND price_date = ?", (CRISIS_START.isoformat(),)).fetchone()["price"]
        self.assertEqual(first, MARKET["005930.KS"]["2008-09-12"])

    def test_kosdaq_names_fall_back_to_kospi_when_the_kosdaq_index_is_short(self):
        market = {**MARKET, "^KQ11": falling(KRX[40:], 500.0, 300.0)}
        connection = database()
        refresh_crisis_replay(connection, account_id="KIWOOM-1", reporting_currency="KRW",
                              ticker_factory=lambda symbol: _Ticker(market.get(symbol, {})))
        rows = {r["holding_symbol"]: r["source_symbol"] for r in
                connection.execute("SELECT * FROM crisis_replay_components")}
        self.assertEqual(rows["123456"], "^KS11")

    def test_a_holding_in_another_currency_is_refused(self):
        connection = database()
        connection.execute("UPDATE position_snapshots SET currency = 'USD' WHERE conid = 'K3'")
        with self.assertRaisesRegex(RuntimeError, "won replay takes KRW holdings only"):
            refresh_crisis_replay(connection, account_id="KIWOOM-1", reporting_currency="KRW",
                                  ticker_factory=lambda symbol: _Ticker(MARKET.get(symbol, {})))


if __name__ == "__main__":
    unittest.main()
