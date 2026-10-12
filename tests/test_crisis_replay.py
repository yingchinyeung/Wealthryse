from __future__ import annotations

import unittest
import sqlite3
from datetime import date
from unittest.mock import Mock, patch

from ibkr_etl.crisis_replay import (
    CALCULATION_VERSION,
    CRISIS_END,
    CRISIS_START,
    ReplayComponent,
    _align_to_calendar,
    calculate_fixed_unit_replay,
    convert_crisis_prices_to_usd,
    fetch_adjusted_history,
    load_crisis_replay,
    refresh_composite_crisis_replay,
    resolve_holdings_as_of,
)


class _RawPsycopgCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _RawPsycopgConnection:
    __module__ = "psycopg"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, parameters: tuple):
        self.calls.append((sql, parameters))
        if "FROM platform_subjects" in sql:
            return _RawPsycopgCursor({"subject_type": "physical_account"})
        if "MAX(p.snapshot_date)" in sql:
            return _RawPsycopgCursor({"latest": "2026-09-18"})
        raise AssertionError(sql)


class CrisisReplayTests(unittest.TestCase):
    def test_cross_market_opening_holiday_uses_prior_close_without_lookahead(self) -> None:
        aligned = _align_to_calendar(
            {
                "2008-09-12": 100.0,
                "2008-09-16": 90.0,
            },
            ["2008-09-15", "2008-09-16"],
        )

        self.assertEqual(aligned["2008-09-15"], 100.0)
        self.assertEqual(aligned["2008-09-16"], 90.0)

    def test_cross_market_opening_holiday_never_uses_future_close(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-forward-looking"):
            _align_to_calendar(
                {"2008-09-16": 90.0},
                ["2008-09-15", "2008-09-16"],
            )

    def test_adjusted_history_can_fetch_a_governed_opening_lookback(self) -> None:
        calls: list[dict] = []

        class _Index:
            def __init__(self, value: str) -> None:
                self.value = value

            def date(self) -> date:
                return date.fromisoformat(self.value)

        class _Frame:
            def iterrows(self):
                return iter(
                    [
                        (_Index("2008-09-12"), {"Adj Close": 100.0}),
                        (_Index("2008-09-16"), {"Adj Close": 90.0}),
                    ]
                )

        class _Ticker:
            def history(self, **kwargs):
                calls.append(kwargs)
                return _Frame()

        result = fetch_adjusted_history(
            "^HSI",
            lambda _symbol: _Ticker(),
            start_date=date(2008, 9, 5),
            end_date=CRISIS_END,
        )

        self.assertEqual(calls[0]["start"], "2008-09-05")
        self.assertEqual(calls[0]["end"], "2009-03-10")
        self.assertEqual(result["2008-09-12"], 100.0)
        self.assertNotIn(CRISIS_START.isoformat(), result)

    def test_composite_builds_missing_physical_component_replay_on_demand(self) -> None:
        connection = Mock()
        connection.commit = Mock()

        class _Cursor:
            def __init__(self, *, row=None, rows=None):
                self._row = row
                self._rows = rows or []

            def fetchone(self):
                return self._row

            def fetchall(self):
                return self._rows

        def execute(_connection, sql, parameters):
            if "SELECT total_text FROM subject_nav_series" in sql:
                return _Cursor(row={"total_text": "100"})
            if "FROM synthetic_portfolio_components" in sql:
                return _Cursor(
                    rows=[
                        {
                            "component_id": "U16088571",
                            "component_name": "US sleeve",
                            "component_type": "account",
                            "direction": "long",
                            "contribution": 100.0,
                            "source_currency": "USD",
                        }
                    ]
                )
            if "SELECT observation_date, normalized_value" in sql:
                return _Cursor(
                    rows=[
                        {"observation_date": "2008-09-15", "normalized_value": 1.0},
                        {"observation_date": "2009-03-09", "normalized_value": 0.8},
                    ]
                )
            return _Cursor()

        rebuilt = {
            "subject_id": "U16088571",
            "holdings_as_of": "2025-01-31",
            "observations": 2,
        }
        loaded = {
            "subject_id": "alpha-world-ms",
            "holdings_as_of": "2025-01-31",
            "observations": 2,
        }

        def load_side_effect(_connection, *, account_id, as_of):
            del _connection, as_of
            return loaded if account_id == "alpha-world-ms" else None

        with (
            patch("ibkr_etl.crisis_replay.resolve_holdings_as_of", return_value="2025-01-31"),
            patch("ibkr_etl.crisis_replay._execute", side_effect=execute),
            patch("ibkr_etl.crisis_replay.load_crisis_replay", side_effect=load_side_effect),
            patch("ibkr_etl.crisis_replay.refresh_crisis_replay", return_value=rebuilt) as refresh,
        ):
            result = refresh_composite_crisis_replay(
                connection,
                account_id="alpha-world-ms",
                holdings_as_of="2025-01-31",
                price_cache={
                    "SPY": {"2008-09-15": 100.0, "2009-03-09": 50.0}
                },
                persisted_price_symbols=set(),
            )

        self.assertEqual(result, loaded)
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.kwargs["account_id"], "U16088571")
        self.assertEqual(refresh.call_args.kwargs["holdings_as_of"], "2025-01-31")

    def test_native_crisis_prices_are_converted_to_usd_before_normalization(self) -> None:
        converted = convert_crisis_prices_to_usd(
            {"2008-09-15": 100.0, "2008-09-16": 80.0},
            "TWD",
            [("2008-09-15", 0.031), ("2008-09-16", 0.029)],
        )
        self.assertAlmostEqual(converted["2008-09-15"], 3.1)
        self.assertAlmostEqual(converted["2008-09-16"], 2.32)
        self.assertNotAlmostEqual(
            converted["2008-09-16"] / converted["2008-09-15"], 0.8
        )

    def test_native_crisis_prices_fail_closed_without_fx(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "TWD/USD FX series"):
            convert_crisis_prices_to_usd({"2008-09-15": 100.0}, "TWD")

    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE position_snapshots(account_id TEXT, snapshot_date TEXT);
            CREATE TABLE nav_snapshots(
                account_id TEXT, snapshot_date TEXT, asset_class TEXT
            );
            CREATE TABLE crisis_replay_daily(
                subject_id TEXT, holdings_as_of TEXT, observation_date TEXT,
                drawdown REAL, crisis_start TEXT, crisis_end TEXT,
                calculation_version TEXT, generated_at TEXT
            );
            CREATE TABLE crisis_replay_components(
                subject_id TEXT, holdings_as_of TEXT, mapping_method TEXT,
                opening_weight REAL, provider TEXT, price_type TEXT
            );
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_fixed_units_and_flat_cash_calculate_maximum_drawdown(self) -> None:
        component = ReplayComponent(
            conid="1", symbol="NEW", description="New holding", currency="USD",
            market_value=80.0, source_symbol="SPY", mapping_method="market_proxy",
            proxy_reason="holding was unavailable",
        )
        result = calculate_fixed_unit_replay(
            components=[component],
            portfolio_nav=100.0,
            prices={
                "SPY": {
                    "2008-09-15": 100.0,
                    "2008-09-16": 75.0,
                    "2009-03-09": 50.0,
                }
            },
            calendar=["2008-09-15", "2008-09-16", "2009-03-09"],
        )
        self.assertAlmostEqual(result["cash_weight"], 0.2)
        self.assertAlmostEqual(result["daily"][0]["normalized_value"], 1.0)
        self.assertAlmostEqual(result["daily"][-1]["normalized_value"], 0.6)
        self.assertAlmostEqual(result["maximum_drawdown"], -0.4)

    def test_replay_fails_closed_when_a_component_price_is_missing(self) -> None:
        component = ReplayComponent(
            conid="1", symbol="A", description="A", currency="USD",
            market_value=100.0, source_symbol="A", mapping_method="direct",
            proxy_reason=None,
        )
        with self.assertRaisesRegex(ValueError, "Incomplete replay prices"):
            calculate_fixed_unit_replay(
                components=[component], portfolio_nav=100.0,
                prices={"A": {"2008-09-15": 100.0, "2009-03-09": 50.0}},
                calendar=["2008-09-15", "2008-09-16", "2009-03-09"],
            )

    def test_holdings_resolution_uses_latest_common_date_at_cutoff(self) -> None:
        self.connection.executemany(
            "INSERT INTO position_snapshots VALUES ('U1', ?)",
            [("2026-08-28",), ("2026-08-31",), ("2026-09-18",)],
        )
        self.connection.executemany(
            "INSERT INTO nav_snapshots VALUES ('U1', ?, 'TOTAL')",
            [("2026-08-28",), ("2026-09-18",)],
        )
        self.assertEqual(
            resolve_holdings_as_of(
                self.connection, account_id="U1", as_of="2026-08-31"
            ),
            "2026-08-28",
        )
        self.assertEqual(
            resolve_holdings_as_of(
                self.connection, account_id="U1", as_of="2026-09-30"
            ),
            "2026-09-18",
        )

    def test_holdings_resolution_translates_all_raw_psycopg_placeholders(self) -> None:
        connection = _RawPsycopgConnection()

        self.assertEqual(
            resolve_holdings_as_of(
                connection, account_id="U16088606", as_of="2026-09-25"
            ),
            "2026-09-18",
        )
        self.assertEqual(len(connection.calls), 2)
        for sql, _ in connection.calls:
            self.assertNotIn("?", sql)
            self.assertIn("%s", sql)

    def test_replay_loader_never_uses_holdings_after_requested_date(self) -> None:
        for holdings_as_of, drawdown in (
            ("2026-08-31", -0.31),
            ("2026-09-18", -0.45),
        ):
            self.connection.execute(
                "INSERT INTO position_snapshots VALUES ('U1', ?)",
                (holdings_as_of,),
            )
            self.connection.execute(
                "INSERT INTO nav_snapshots VALUES ('U1', ?, 'TOTAL')",
                (holdings_as_of,),
            )
            self.connection.executemany(
                "INSERT INTO crisis_replay_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "U1", holdings_as_of, "2008-09-15", 0.0,
                        "2008-09-15", "2009-03-09", CALCULATION_VERSION,
                        "2026-09-23T00:00:00+00:00",
                    ),
                    (
                        "U1", holdings_as_of, "2009-03-09", drawdown,
                        "2008-09-15", "2009-03-09", CALCULATION_VERSION,
                        "2026-09-23T00:00:00+00:00",
                    ),
                ],
            )
            self.connection.execute(
                "INSERT INTO crisis_replay_components VALUES (?, ?, 'direct', 0.8, 'test', 'adjusted_close')",
                ("U1", holdings_as_of),
            )
        replay = load_crisis_replay(
            self.connection, account_id="U1", as_of="2026-08-31"
        )
        self.assertEqual(replay["holdings_as_of"], "2026-08-31")
        self.assertAlmostEqual(replay["maximum_drawdown"], -0.31)
        self.assertAlmostEqual(replay["cash_weight"], 0.2)

    def test_replay_loader_does_not_fall_back_to_an_older_holdings_mix(self) -> None:
        for observed in ("2026-08-29", "2026-08-31"):
            self.connection.execute(
                "INSERT INTO position_snapshots VALUES ('U1', ?)", (observed,)
            )
            self.connection.execute(
                "INSERT INTO nav_snapshots VALUES ('U1', ?, 'TOTAL')", (observed,)
            )
        self.connection.execute(
            "INSERT INTO crisis_replay_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "U1", "2026-08-29", "2008-09-15", 0.0,
                "2008-09-15", "2009-03-09", CALCULATION_VERSION,
                "2026-09-23T00:00:00+00:00",
            ),
        )
        self.assertIsNone(
            load_crisis_replay(
                self.connection, account_id="U1", as_of="2026-08-31"
            )
        )

    def test_replay_loader_returns_none_before_first_available_date(self) -> None:
        self.assertIsNone(
            load_crisis_replay(
                self.connection, account_id="U1", as_of="2025-01-31"
            )
        )


if __name__ == "__main__":
    unittest.main()
