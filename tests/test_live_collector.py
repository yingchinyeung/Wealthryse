"""The live collector against a replayed IB Gateway (no network, no ib_async).

tests/fixtures/live_gateway_replay.json holds synthetic gateway responses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ibkr_etl import live_collector as live
from ibkr_etl import live_collector_access as access

ROOT = Path(__file__).resolve().parents[1]
REPLAY = json.loads((ROOT / "tests/fixtures/live_gateway_replay.json").read_text(encoding="utf-8"))
ACCOUNTS = ("U1000001", "U1000002")
TENANT = "00000000-0000-0000-0000-000000000001"
# Tuesday 13 October 2026, 22:35 in Hong Kong and 10:35 in New York (US open).
US_OPEN = datetime(2026, 10, 13, 14, 35, tzinfo=timezone.utc)
logging.getLogger("ibkr_etl.live_collector").addHandler(logging.NullHandler())


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args):
        for handler in self.handlers:
            handler(*args)


def _ns(mapping):
    return SimpleNamespace(**mapping)


class FakeIB:
    """Answers the calls the collector makes from the replay file."""

    def __init__(self, replay=REPLAY, managed=None):
        self.replay = replay
        self.managed = list(replay["managed_accounts"] if managed is None else managed)
        self.errorEvent = Event()
        self.disconnectedEvent = Event()
        self.updateEvent = Event()
        self.connected = False
        self.fail_connect = False
        self.connect_calls = []
        self.market_data_types = []
        self.pnl_requests = []
        self.mkt_requests = []
        self.cancelled = []
        self.history_requests = []
        self._pnl = {}
        self.RequestTimeout = 0

    def __getattr__(self, name):
        # Any call the replay does not cover, such as an order method, fails the tests.
        raise AssertionError(f"unexpected IB call: {name}")

    def isConnected(self):
        return self.connected

    async def connectAsync(self, host, port, clientId, timeout, readonly):
        self.connect_calls.append({"host": host, "port": port, "clientId": clientId, "readonly": readonly})
        if self.fail_connect:
            raise ConnectionRefusedError("gateway not listening")
        self.connected = True

    def disconnect(self):
        self.connected = False
        self._pnl.clear()
        self.disconnectedEvent.emit()

    def managedAccounts(self):
        return list(self.managed)

    def reqMarketDataType(self, value):
        self.market_data_types.append(value)

    def pnl(self):
        return list(self._pnl.values())

    def reqPnL(self, account, modelCode=""):
        assert account not in self._pnl
        self.pnl_requests.append(account)
        values = self.replay["pnl"].get(account, {})
        self._pnl[account] = SimpleNamespace(account=account, modelCode="", **values)
        return self._pnl[account]

    async def accountSummaryAsync(self, account=""):
        return [_ns(row) for row in self.replay["account_summary"]]

    def positions(self):
        return [
            SimpleNamespace(account=row["account"], position=row["position"], avgCost=row["avgCost"],
                            contract=_ns(row["contract"]))
            for row in self.replay["positions"]
        ]

    async def reqContractDetailsAsync(self, contract):
        key = str(contract.conId) if getattr(contract, "conId", 0) else f"{contract.secType}:{contract.symbol}"
        details = self.replay["contract_details"].get(key)
        if details is None:
            return []
        return [SimpleNamespace(**{k: v for k, v in details.items() if k != "contract"},
                                contract=_ns(details["contract"]))]

    def reqMktData(self, contract, genericTickList="", snapshot=False):
        self.mkt_requests.append(contract)
        values = self.replay["tickers"].get(str(contract.conId), {"marketDataType": 1})
        return SimpleNamespace(contract=contract, **values)

    def cancelMktData(self, contract):
        self.cancelled.append(contract.conId)

    async def reqExecutionsAsync(self):
        fills = []
        for row in self.replay["fills"]:
            execution = dict(row["execution"], time=datetime.fromisoformat(row["execution"]["time"]))
            fills.append(SimpleNamespace(contract=_ns(row["contract"]), execution=_ns(execution),
                                         commissionReport=_ns(row["commissionReport"]), time=execution["time"]))
        return fills

    async def reqHistoricalDataAsync(self, contract, **options):
        self.history_requests.append((contract, options))
        return [SimpleNamespace(**dict(bar, date=date.fromisoformat(bar["date"])))
                for bar in self.replay["bars"].get(str(contract.conId), [])]


class RecordingStore:
    def __init__(self):
        self.status, self.snapshots, self.quotes = [], [], []
        self.executions, self.contracts, self.bars, self.purges = [], [], [], []
        self.bar_dates = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise ConnectionError("database unavailable")

    def write_status(self, row):
        self._check()
        self.status.append(row)

    def write_snapshot(self, account, positions):
        self._check()
        self.snapshots.append((account, positions))

    def write_quotes(self, rows):
        self._check()
        self.quotes.extend(rows)

    def write_executions(self, rows):
        self._check()
        self.executions.extend(rows)

    def write_contracts(self, rows):
        self._check()
        self.contracts.extend(rows)

    def write_bars(self, rows):
        self._check()
        self.bars.extend(rows)

    def latest_bar_dates(self):
        self._check()
        return dict(self.bar_dates)

    def purge(self, now):
        self._check()
        self.purges.append(now)

    def close(self):
        pass


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment += timedelta(**delta)


def _settings(status_file=None, **overrides):
    values = dict(
        gateway_host="ib-gateway", gateway_port=4003, client_id=17, account_ids=ACCOUNTS,
        tenant_id=TENANT, database_url="postgresql://collector@db/test", market_data_type=3,
        status_file=status_file,
        benchmarks=(live.parse_benchmark("IND:SPX:CBOE:USD"), live.parse_benchmark("IND:COMP:NASDAQ:USD")),
    )
    values.update(overrides)
    return live.CollectorSettings(**values)


def _collector(ib=None, store=None, clock=None, **settings):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    collector = live.LiveCollector(
        _settings(**settings), ib or FakeIB(), store or RecordingStore(),
        clock=clock or Clock(US_OPEN), contract_factory=lambda **fields: SimpleNamespace(**fields), sleep=sleep,
    )
    collector.test_sleeps = sleeps
    return collector


def _run(coroutine):
    return asyncio.run(coroutine)


def _tick(collector):
    async def tick_and_finish_bars():
        await collector.tick()
        await collector.drain()

    asyncio.run(tick_and_finish_bars())


class SettingsTests(unittest.TestCase):
    def _env(self, directory, **overrides):
        url_file = Path(directory) / "database-live-url"
        url_file.write_text("postgresql://ibkr_live_collector@db.example/ibkr_staging\n", encoding="utf-8")
        env = {
            "IB_GATEWAY_HOST": "ib-gateway", "IB_GATEWAY_PORT": "4003",
            "LIVE_ACCOUNT_IDS": "U1000001, U1000002", "PORTFOLIO_TENANT_ID": TENANT,
            "LIVE_DATABASE_URL_FILE": str(url_file),
        }
        env.update(overrides)
        return env

    def test_reads_environment_and_url_file(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = live.CollectorSettings.from_environment(self._env(directory))
        self.assertEqual(settings.account_ids, ACCOUNTS)
        self.assertEqual(settings.client_id, 17)
        self.assertEqual(settings.market_data_type, 3)
        self.assertEqual(settings.database_url, "postgresql://ibkr_live_collector@db.example/ibkr_staging")
        self.assertEqual([b.symbol for b in settings.benchmarks], ["SPX", "COMP"])
        self.assertNotIn("ibkr_live_collector@", repr(settings))

    def test_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as directory:
            for key, value in [
                ("LIVE_ACCOUNT_IDS", "U1000001;DROP"), ("LIVE_ACCOUNT_IDS", ""),
                ("IB_GATEWAY_PORT", "0"), ("PORTFOLIO_TENANT_ID", "tenant"),
                ("LIVE_MARKET_DATA_TYPE", "5"), ("LIVE_BENCHMARKS", "SPX"),
            ]:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    live.CollectorSettings.from_environment(self._env(directory, **{key: value}))

    def test_database_url_must_be_postgres(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self._env(directory)
            Path(env["LIVE_DATABASE_URL_FILE"]).write_text("sqlite:///x.db", encoding="utf-8")
            with self.assertRaises(ValueError):
                live.CollectorSettings.from_environment(env)


class RowTests(unittest.TestCase):
    def test_number_drops_unset_markers(self):
        for value in (None, "", "abc", float("nan"), float("inf"), live.UNSET_DOUBLE, live.UNSET_INTEGER):
            self.assertIsNone(live.number(value))
        self.assertEqual(live.number("12.5"), 12.5)
        self.assertEqual(live.number(0), 0.0)

    def test_account_row(self):
        summary = [_ns(row) for row in REPLAY["account_summary"]]
        pnl = SimpleNamespace(**REPLAY["pnl"]["U1000001"])
        row = live.account_row("U1000001", summary, pnl, US_OPEN)
        self.assertEqual(row["base_currency"], "HKD")
        self.assertEqual(row["net_liquidation"], 1250000.5)
        self.assertEqual(row["total_cash"], 50000.0)
        self.assertEqual(row["cash_by_currency"], {"HKD": 30000.0, "USD": 2564.1})
        self.assertEqual(row["daily_pnl"], 1234.5)
        self.assertIsNone(row["realized_pnl"])

    def test_account_without_net_liquidation_is_not_a_snapshot(self):
        summary = [_ns(row) for row in REPLAY["account_summary"] if row["tag"] != "NetLiquidation"]
        self.assertIsNone(live.account_row("U1000001", summary, None, US_OPEN))

    def test_position_rows_use_quotes_in_the_instrument_currency(self):
        ib = FakeIB()
        tickers = {conid: SimpleNamespace(**values) for conid, values in REPLAY["tickers"].items()}
        rows = {row["conid"]: row for row in live.position_rows("U1000001", ib.positions(), tickers, US_OPEN)}
        self.assertEqual(set(rows), {"265598", "2042", "700000001", "900001"})  # no closed or other-account rows
        apple = rows["265598"]
        self.assertEqual((apple["market_price"], apple["market_value"]), (190.5, 19050.0))
        self.assertAlmostEqual(apple["unrealized_pnl"], 4025.0)
        self.assertAlmostEqual(apple["daily_pnl"], 250.0)
        tencent = rows["2042"]  # no last trade: bid/ask midpoint
        self.assertAlmostEqual(tencent["market_price"], 380.2)
        self.assertAlmostEqual(tencent["unrealized_pnl"], 500 * 380.2 - 160000)
        option = rows["700000001"]  # cost and value carry the 100 multiplier
        self.assertEqual(option["average_cost"], 5.12)
        self.assertEqual(option["market_value"], 1200.0)
        self.assertAlmostEqual(option["unrealized_pnl"], 176.0)
        fund = rows["900001"]
        self.assertIsNone(fund["market_price"])
        self.assertIsNone(fund["market_value"])
        self.assertEqual(fund["average_cost"], 10.0)

    def test_execution_rows(self):
        fills = _run(FakeIB().reqExecutionsAsync())
        rows = [live.execution_row(fill, ACCOUNTS, US_OPEN) for fill in fills]
        bought, sold, other = rows
        self.assertEqual((bought["side"], bought["quantity"], bought["commission"]), ("BUY", 10.0, 1.0))
        self.assertIsNone(bought["realized_pnl"])  # IBKR's unset marker
        self.assertEqual((sold["side"], sold["commission"], sold["commission_currency"]), ("SELL", None, None))
        self.assertEqual(sold["executed_at"], datetime(2026, 10, 13, 2, 15, tzinfo=timezone.utc))
        self.assertIsNone(other)  # not a configured account

    def test_bar_rows_skip_bad_values(self):
        bars = [SimpleNamespace(date=date(2026, 10, 12), open=1.0, high=2.0, low=0.5, close=1.5, volume=-1),
                SimpleNamespace(date=date(2026, 10, 13), open=1.0, high=2.0, low=0.5, close=float("nan"), volume=5)]
        rows = live.bar_rows("416904", "USD", "TRADES", bars, US_OPEN)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["volume"])

    def test_market_hours(self):
        hk_morning = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)  # 10:00 HKT Wednesday
        quiet = datetime(2026, 10, 14, 9, 30, tzinfo=timezone.utc)  # 17:30 HKT, 05:30 New York
        saturday = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
        self.assertTrue(live.markets_open(US_OPEN))
        self.assertTrue(live.markets_open(hk_morning))
        self.assertFalse(live.markets_open(quiet))
        self.assertFalse(live.markets_open(saturday))
        self.assertEqual(live.snapshot_interval(quiet), 300)

    def test_latest_slot(self):
        self.assertEqual(live.latest_slot(US_OPEN, live.BAR_RUN_TIMES_HKT),
                         datetime(2026, 10, 13, 8, 45, tzinfo=timezone.utc))  # 16:45 HKT
        early = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)  # 02:00 HKT on the 14th
        self.assertEqual(live.latest_slot(early, (live.PURGE_TIME_HKT,)),
                         datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc))


class CollectorTests(unittest.TestCase):
    def test_first_tick_connects_read_only_and_writes_everything(self):
        ib, store = FakeIB(), RecordingStore()
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            collector = _collector(ib, store, status_file=status_file)
            _tick(collector)
            status = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(ib.connect_calls, [{"host": "ib-gateway", "port": 4003, "clientId": 17, "readonly": True}])
        self.assertEqual(ib.market_data_types, [3])
        self.assertEqual(sorted(ib.pnl_requests), list(ACCOUNTS))
        self.assertEqual(status["gateway_state"], "connected")
        self.assertEqual(status["observed_at"], "2026-10-13T14:35:00+00:00")
        accounts = {account["account_id"]: positions for account, positions in store.snapshots}
        self.assertEqual(set(accounts), set(ACCOUNTS))
        self.assertEqual([row["conid"] for row in accounts["U1000002"]], ["756733"])
        # Benchmarks first, then holdings by cost; the fund has no market data.
        quoted = [str(contract.conId) for contract in ib.mkt_requests]
        self.assertEqual(quoted, ["12345678", "416904", "2042", "265598", "756733", "700000001"])
        exchanges = {str(c.conId): c.exchange for c in ib.mkt_requests}
        self.assertEqual((exchanges["416904"], exchanges["2042"]), ("CBOE", "SMART"))
        self.assertNotIn("12345678", {row["conid"] for row in store.quotes})  # nothing priced yet
        self.assertEqual({row["market_data_type"] for row in store.quotes if row["conid"] == "2042"}, {3})
        self.assertEqual([row["exec_id"] for row in store.executions],
                         ["0000e0d5.6705a1b2.01.01", "0000e0d5.6705a1b3.01.01"])
        benchmarks = {row["conid"] for row in store.contracts if row["is_benchmark"]}
        self.assertEqual(benchmarks, {"416904", "12345678"})
        self.assertEqual(store.status[-1]["gateway_state"], "connected")
        self.assertEqual(store.status[-1]["market_data_lines_in_use"], 6)
        self.assertEqual(len(store.purges), 1)

    def test_bars_are_paced_and_use_adjusted_closes_for_stocks(self):
        ib, store = FakeIB(), RecordingStore()
        store.bar_dates = {("265598", "ADJUSTED_LAST"): date(2026, 10, 9)}
        collector = _collector(ib, store)
        _tick(collector)
        requests = {str(contract.conId): options for contract, options in ib.history_requests}
        self.assertEqual(set(requests), {"265598", "2042", "756733", "416904", "12345678"})
        self.assertEqual(requests["265598"]["whatToShow"], "ADJUSTED_LAST")
        self.assertEqual(requests["265598"]["durationStr"], "14 D")
        self.assertEqual(requests["2042"]["durationStr"], "1 Y")
        self.assertEqual(requests["416904"]["whatToShow"], "TRADES")
        self.assertEqual(collector.test_sleeps, [live.BAR_REQUEST_SPACING_SECONDS] * 4)
        self.assertEqual({(row["conid"], row["what_to_show"]) for row in store.bars},
                         {("265598", "ADJUSTED_LAST"), ("416904", "TRADES")})
        # Bars run once per slot.
        ib.history_requests.clear()
        collector.clock.advance(minutes=1)
        _tick(collector)
        self.assertEqual(ib.history_requests, [])

    def test_unchanged_quotes_are_not_rewritten_and_closed_markets_slow_down(self):
        ib, store = FakeIB(), RecordingStore()
        clock = Clock(US_OPEN)
        collector = _collector(ib, store, clock)
        _tick(collector)
        first_quotes = len(store.quotes)
        clock.advance(minutes=1)
        _tick(collector)
        self.assertEqual(len(store.snapshots), 4)
        self.assertEqual(len(store.quotes), first_quotes)
        weekend = Clock(datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc))
        quiet = _collector(FakeIB(), RecordingStore(), weekend)
        _tick(quiet)
        weekend.advance(minutes=1)
        _tick(quiet)
        self.assertEqual(len(quiet.store.snapshots), 2)  # one per account, then nothing for 5 minutes
        weekend.advance(minutes=4)
        _tick(quiet)
        self.assertEqual(len(quiet.store.snapshots), 4)
        self.assertEqual(len(quiet.store.status), 3)

    def test_disconnect_reconnects_and_resubscribes(self):
        ib, store = FakeIB(), RecordingStore()
        clock = Clock(US_OPEN)
        collector = _collector(ib, store, clock)
        _tick(collector)
        ib.disconnect()
        self.assertEqual(collector.gateway_state(), "disconnected")
        clock.advance(minutes=1)
        _tick(collector)
        self.assertEqual(len(ib.connect_calls), 2)
        self.assertEqual(ib.pnl_requests.count("U1000001"), 2)
        self.assertEqual(store.status[-1]["gateway_state"], "connected")

    def test_failed_connection_backs_off_and_reports_disconnected(self):
        ib, store = FakeIB(), RecordingStore()
        ib.fail_connect = True
        clock = Clock(US_OPEN)
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            collector = _collector(ib, store, clock, status_file=status_file)
            _tick(collector)
            self.assertEqual(json.loads(status_file.read_text())["gateway_state"], "disconnected")
        clock.advance(seconds=20)
        _tick(collector)
        self.assertEqual(len(ib.connect_calls), 1)  # waits 30 seconds after the first failure
        clock.advance(seconds=20)
        _tick(collector)
        self.assertEqual(len(ib.connect_calls), 2)
        self.assertEqual([row["gateway_state"] for row in store.status], ["disconnected"] * 3)
        self.assertEqual(store.snapshots, [])

    def test_gateway_server_connectivity_errors(self):
        collector = _collector()
        _tick(collector)
        collector.ib.errorEvent.emit(-1, 2104, "Market data farm connection is OK:hfarm", None)
        self.assertIsNone(collector.last_error)
        collector.ib.errorEvent.emit(-1, 1100, "Connectivity between IBKR and Trader Workstation has been lost.", None)
        self.assertEqual(collector.gateway_state(), "disconnected")
        self.assertEqual(collector.status_row(US_OPEN)["last_error_code"], 1100)
        collector.ib.errorEvent.emit(-1, 1102, "Connectivity restored - data maintained." + "x" * 400, None)
        self.assertEqual(collector.gateway_state(), "connected")
        self.assertEqual(len(collector.status_row(US_OPEN)["last_error_message"]), 300)

    def test_account_missing_from_the_login_is_reported_and_skipped(self):
        ib, store = FakeIB(managed=["U1000001"]), RecordingStore()
        collector = _collector(ib, store)
        _tick(collector)
        self.assertEqual(ib.pnl_requests, ["U1000001"])
        self.assertEqual([account["account_id"] for account, _ in store.snapshots], ["U1000001"])
        self.assertIn("U1000002", store.status[-1]["last_error_message"])

    def test_database_outage_does_not_stop_the_status_file(self):
        ib, store = FakeIB(), RecordingStore()
        store.fail = True
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            collector = _collector(ib, store, status_file=status_file)
            _tick(collector)
            self.assertEqual(json.loads(status_file.read_text())["gateway_state"], "connected")
        store.fail = False
        collector.clock.advance(minutes=1)
        _tick(collector)
        self.assertEqual(len(store.snapshots), 2)

    def test_market_data_lines_are_capped(self):
        ib = FakeIB()
        collector = _collector(ib, max_market_data_lines=3)
        _tick(collector)
        self.assertEqual(len(ib.mkt_requests), 3)
        self.assertEqual(collector.status_row(US_OPEN)["market_data_lines_in_use"], 3)

    def test_sold_holding_drops_its_quote(self):
        replay = json.loads(json.dumps(REPLAY))
        ib = FakeIB(replay)
        collector = _collector(ib)
        _tick(collector)
        replay["positions"] = [row for row in replay["positions"] if row["contract"]["conId"] != 2042]
        collector.clock.advance(minutes=1)
        _tick(collector)
        self.assertEqual(ib.cancelled, [2042])


class SafetyTests(unittest.TestCase):
    def test_collector_has_no_order_calls(self):
        source = (ROOT / "src/ibkr_etl/live_collector.py").read_text(encoding="utf-8")
        for name in ("placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "bracketOrder",
                     "reqOpenOrders", "reqAutoOpenOrders", "exerciseOptions"):
            self.assertNotIn(name, source)
        self.assertIn("readonly=True", source)

    def test_collector_grants_cover_only_the_live_tables(self):
        statements = access.grant_statements()
        tables = {re.search(r"ON TABLE public\.(\w+)", s).group(1) for s in statements if "ON TABLE" in s}
        self.assertEqual(tables, set(access.LIVE_TABLES))
        migration = (ROOT / "migrations/postgres/063_live_ibkr_data.sql").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r"CREATE TABLE (live_\w+)", migration)), set(access.LIVE_TABLES))
        self.assertTrue(all("ALL" not in s and "ROLE" not in s for s in statements))
        with self.assertRaises(ValueError):
            access.grant_statements("ibkr_web_reader")

    def test_least_privilege_check(self):
        good = {"writable": list(access.LIVE_TABLES), "readable": list(access.LIVE_TABLES),
                "privileged_attributes": False}
        self.assertEqual(access.least_privilege_problems(good), [])
        wide = dict(good, readable=list(access.LIVE_TABLES) + ["accounts"], privileged_attributes=True)
        self.assertEqual(len(access.least_privilege_problems(wide)), 2)


if __name__ == "__main__":
    unittest.main()
