from __future__ import annotations

import json

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ibkr_etl.db import connect, initialize
from ibkr_etl.derived_portfolio import (
    refresh_alpha_world_ms_portfolio,
    refresh_alpha_world_t_portfolio,
    refresh_filtered_portfolio,
    refresh_fixed_unit_composite,
    refresh_hong_kong_china_portfolio,
)
from ibkr_etl.report import _report_payload
from ibkr_etl.subject_onboarding import (
    filtered_portfolio_definition,
    fixed_unit_composite_definition,
)


class DerivedPortfolioTests(unittest.TestCase):
    def test_alpha_world_ms_fixed_unit_formula_and_market_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "portfolio.db"
            initialize(database)
            connection = connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO ingestion_runs(
                        run_type, requested_from, requested_to, started_at,
                        completed_at, status, row_count, reconciliation_json
                    ) VALUES ('test', '2025-01-20', '2025-01-21',
                              '2025-01-21T08:00:00Z', '2025-01-21T08:01:00Z',
                              'success', 10, '{"status":"PASS"}')
                    """
                )
                run_id = connection.execute(
                    "SELECT run_id FROM ingestion_runs"
                ).fetchone()[0]
                for account_id in ("U16088606", "U16088571"):
                    connection.execute(
                        """
                        INSERT INTO accounts(
                            account_id, alias, base_currency, account_type, ib_entity,
                            first_seen_at, last_seen_at, source_json
                        ) VALUES (?, ?, 'USD', 'Individual', 'IBHK',
                                  '2025-01-20', '2025-01-21', '{}')
                        """,
                        (account_id, account_id),
                    )
                for snapshot_date, nav in (
                    ("2025-01-20", 400.0),
                    ("2025-01-21", 360.0),
                ):
                    connection.execute(
                        """
                        INSERT INTO nav_snapshots(
                            account_id, snapshot_date, currency, asset_class,
                            total_text, cash_text, stock_text, options_text,
                            futures_text, bonds_text, accruals_text, row_sha256,
                            last_seen_run_id
                        ) VALUES ('U16088571', ?, 'USD', 'TOTAL', ?, '0', ?,
                                  '0', '0', '0', '0', ?, ?)
                        """,
                        (snapshot_date, str(nav), str(nav), f"nav-{snapshot_date}", run_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO fx_rates(
                            account_id, rate_date, from_currency, to_currency,
                            rate_text, row_sha256, last_seen_run_id
                        ) VALUES ('U16088571', ?, 'HKD', 'USD', '0.128', ?, ?)
                        """,
                        (snapshot_date, f"fx-{snapshot_date}", run_id),
                    )
                for snapshot_date, nav in (
                    ("2025-01-20", 300.0),
                    ("2025-01-21", 360.0),
                ):
                    connection.execute(
                        """
                        INSERT INTO nav_snapshots(
                            account_id, snapshot_date, currency, asset_class,
                            total_text, cash_text, stock_text, options_text,
                            futures_text, bonds_text, accruals_text, row_sha256,
                            last_seen_run_id
                        ) VALUES ('U16088606', ?, 'USD', 'TOTAL', ?, '0', ?,
                                  '0', '0', '0', '0', ?, ?)
                        """,
                        (
                            snapshot_date,
                            str(nav),
                            str(nav),
                            f"nav-u16088606-{snapshot_date}",
                            run_id,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO derived_portfolios(
                        portfolio_id, name, source_account_id, base_currency,
                        exclusion_rule_json, calculation_version, status,
                        created_at, updated_at
                    ) VALUES ('hong-kong-china', 'China & Hong Kong',
                              'U16088606', 'USD', '{}', 'test', 'active',
                              '2025-01-20', '2025-01-21')
                    """
                )
                for snapshot_date, nav in (
                    ("2025-01-20", 200.0),
                    ("2025-01-21", 220.0),
                ):
                    connection.execute(
                        """
                        INSERT INTO derived_portfolio_nav(
                            portfolio_id, snapshot_date, currency, total_nav,
                            cash, stock, options, futures, bonds, accruals,
                            source_total_nav, excluded_market_value_base,
                            source_position_count, included_position_count,
                            excluded_position_count, generated_at
                        ) VALUES ('hong-kong-china', ?, 'USD', ?, 0, ?, 0, 0,
                                  0, 0, ?, 0, 1, 1, 0, '2025-01-21')
                        """,
                        (snapshot_date, nav, nav, nav),
                    )
                prices = (
                    ("hang-seng-price-index", "2025-01-20", "^HSI", "Hang Seng Index", "price_index_close", "HKD", 20000.0),
                    ("hang-seng-price-index", "2025-01-21", "^HSI", "Hang Seng Index", "price_index_close", "HKD", 20200.0),
                    ("spy-settlement", "2025-01-17", "SPY", "SPDR S&P 500 ETF Trust", "unadjusted_close", "USD", 500.0),
                    ("spy-settlement", "2025-01-21", "SPY", "SPDR S&P 500 ETF Trust", "unadjusted_close", "USD", 505.0),
                )
                for row in prices:
                    connection.execute(
                        """
                        INSERT INTO market_data_prices(
                            instrument_id, price_date, symbol, instrument_name,
                            price_type, currency, price, source, source_url, fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'test', 'https://example.test',
                                  '2025-01-21')
                        """,
                        row,
                    )
                connection.commit()

                result = refresh_alpha_world_ms_portfolio(
                    connection, generated_at="2025-01-21T16:00:00+08:00"
                )
                self.assertEqual(2, result["nav_dates"])
                self.assertEqual('HKD', result['base_currency'])
                self.assertAlmostEqual(103.66, result["latest_nav"], places=6)
                nav = connection.execute(
                    """
                    SELECT snapshot_date, total_nav, cash
                    FROM derived_portfolio_nav
                    WHERE portfolio_id = 'alpha-world-ms'
                    ORDER BY snapshot_date
                    """
                ).fetchall()
                self.assertAlmostEqual(100.0, nav[0]["total_nav"])
                self.assertAlmostEqual(58.0, nav[1]["cash"])
                self.assertEqual(
                    10,
                    connection.execute(
                        "SELECT COUNT(*) FROM synthetic_portfolio_components"
                    ).fetchone()[0],
                )
                spy_base = connection.execute(
                    """
                    SELECT observation_date
                    FROM synthetic_portfolio_components
                    WHERE portfolio_id = 'alpha-world-ms'
                      AND snapshot_date = '2025-01-20'
                      AND component_id = 'spy-settlement'
                    """
                ).fetchone()
                self.assertEqual("2025-01-17", spy_base["observation_date"])

                # A subsequent HKD refresh must preserve the old USD NAV version.
                connection.execute('''INSERT INTO derived_portfolio_nav(
                    portfolio_id,snapshot_date,currency,total_nav,cash,stock,options,futures,bonds,
                    accruals,source_total_nav,excluded_market_value_base,source_position_count,
                    included_position_count,excluded_position_count,generated_at)
                    SELECT portfolio_id,snapshot_date,'USD',999,cash,stock,options,futures,bonds,
                    accruals,source_total_nav,excluded_market_value_base,source_position_count,
                    included_position_count,excluded_position_count,generated_at
                    FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' AND snapshot_date='2025-01-21' ''')
                connection.commit()
                refresh_alpha_world_ms_portfolio(connection)
                self.assertEqual(999,connection.execute("SELECT total_nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' AND currency='USD'").fetchone()[0])
                # FX moves belong in each sleeve BEFORE its inception normalization.
                connection.execute("UPDATE fx_rates SET rate_text='0.13' WHERE rate_date='2025-01-21'")
                connection.commit()
                changed=refresh_alpha_world_ms_portfolio(connection)
                expected=58+(.91*110+.45*90-.45*101)*.128/.13-.49*101
                self.assertAlmostEqual(changed['latest_nav'],expected,places=10)
                connection.execute("UPDATE fx_rates SET rate_text='0.128' WHERE rate_date='2025-01-21'")
                connection.commit()
                refresh_alpha_world_ms_portfolio(connection)

                payload = _report_payload(
                    connection,
                    datetime(2025, 1, 21, 16, 0, tzinfo=timezone(timedelta(hours=8))),
                    {"risk_free": {}, "sectors": {}},
                )
                alpha = payload["views"]["alpha-world-ms"]
                self.assertEqual(4, alpha["metrics"]["position_count"])
                self.assertAlmostEqual(235.54, alpha["metrics"]["gross_exposure"], places=6)
                self.assertEqual(
                    "fixed_unit_composite",
                    alpha["derived_portfolio"]["portfolio_type"],
                )

                alpha_t_result = refresh_alpha_world_t_portfolio(
                    connection, generated_at="2025-01-21T16:00:01+08:00"
                )
                self.assertEqual(2, alpha_t_result["nav_dates"])
                self.assertEqual('HKD', alpha_t_result['base_currency'])
                self.assertAlmostEqual(112.76, alpha_t_result["latest_nav"], places=6)
                alpha_t_nav = connection.execute(
                    """
                    SELECT snapshot_date, total_nav, cash
                    FROM derived_portfolio_nav
                    WHERE portfolio_id = 'alpha-world-t'
                    ORDER BY snapshot_date
                    """
                ).fetchall()
                self.assertAlmostEqual(100.0, alpha_t_nav[0]["total_nav"])
                self.assertAlmostEqual(58.0, alpha_t_nav[1]["cash"])
                primary_component = connection.execute(
                    """
                    SELECT component_name, normalized_value, contribution
                    FROM synthetic_portfolio_components
                    WHERE portfolio_id = 'alpha-world-t'
                      AND snapshot_date = '2025-01-21'
                      AND component_id = 'U16088606'
                    """
                ).fetchone()
                self.assertEqual(
                    "U16088606-Greater China", primary_component["component_name"]
                )
                self.assertAlmostEqual(120.0, primary_component["normalized_value"])
                self.assertAlmostEqual(109.2, primary_component["contribution"])

                def stored_rows(table):
                    return [tuple(r) for r in connection.execute(f'SELECT * FROM {table}')]

                original_sources = {table: stored_rows(table) for table in
                    ('accounts', 'nav_snapshots', 'fx_rates', 'market_data_prices')}
                ms_before = [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms'")]
                # Independent T FX calculation uses the FULL 120 GC index, not
                # the filtered MS index of 110 or an aggregate USD conversion.
                connection.execute("UPDATE fx_rates SET rate_text='0.13' WHERE rate_date='2025-01-21'")
                connection.commit()
                changed_t = refresh_alpha_world_t_portfolio(connection)
                self.assertAlmostEqual(changed_t['latest_nav'],
                    58 + (.91*120 + .45*90 - .45*101)*.128/.13 - .49*101, places=10)
                connection.execute("UPDATE fx_rates SET rate_text='0.128' WHERE rate_date='2025-01-21'")
                connection.commit()
                connection.execute('''INSERT INTO derived_portfolio_nav(
                    portfolio_id,snapshot_date,currency,total_nav,cash,stock,options,futures,bonds,
                    accruals,source_total_nav,excluded_market_value_base,source_position_count,
                    included_position_count,excluded_position_count,generated_at)
                    SELECT portfolio_id,snapshot_date,'USD',777,cash,stock,options,futures,bonds,
                    accruals,source_total_nav,excluded_market_value_base,source_position_count,
                    included_position_count,excluded_position_count,generated_at
                    FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' AND snapshot_date='2025-01-21' ''')
                connection.commit()
                refresh_alpha_world_t_portfolio(connection)
                self.assertEqual(777, connection.execute("SELECT total_nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' AND currency='USD'").fetchone()[0])
                self.assertEqual(original_sources, {table: stored_rows(table) for table in original_sources})
                self.assertEqual(ms_before, [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms'")])
                from ibkr_etl.subject_onboarding import approved_reference_definitions
                t_before = [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t'")]
                for field, value in [('base_currency', 'USD'), ('constant_cash', 57)]:
                    invalid = approved_reference_definitions()['alpha-world-t']
                    invalid[field] = value
                    with self.assertRaises(ValueError):
                        refresh_fixed_unit_composite(connection, invalid)
                with self.assertRaises(ValueError):
                    refresh_alpha_world_t_portfolio(connection, primary_account_id='hong-kong-china')
                self.assertEqual(t_before, [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t'")])

                payload = _report_payload(
                    connection,
                    datetime(2025, 1, 21, 16, 0, tzinfo=timezone(timedelta(hours=8))),
                    {"risk_free": {}, "sectors": {}},
                )
                alpha_t = payload["views"]["alpha-world-t"]
                self.assertEqual(4, alpha_t["metrics"]["position_count"])
                self.assertEqual(
                    "fixed_unit_composite",
                    alpha_t["derived_portfolio"]["portfolio_type"],
                )
                self.assertIn(
                    "U16088606",
                    [c['component_id'] for c in alpha_t["derived_portfolio"]["exclusion_rule"]["components"]],
                )

                studio_definition = fixed_unit_composite_definition(
                    subject_id="studio-alpha-copy",
                    display_name="Studio Alpha Copy",
                    base_currency="USD",
                    normalization_date="2025-01-20",
                    constant_cash=58.0,
                    components=(
                        {
                            "component_id": "hong-kong-china",
                            "component_name": "China & Hong Kong",
                            "component_type": "portfolio",
                            "coefficient": 0.91,
                            "source_currency": "USD",
                            "price_convention": "nav",
                        },
                        {
                            "component_id": "U16088571",
                            "component_name": "U.S. Technology and Biotechnology",
                            "component_type": "account",
                            "coefficient": 0.45,
                            "source_currency": "USD",
                            "price_convention": "nav",
                        },
                        {
                            "component_id": "hang-seng-price-index",
                            "component_name": "Hang Seng Index",
                            "component_type": "benchmark",
                            "coefficient": -0.49,
                            "source_currency": "HKD",
                            "price_convention": "price_index_close",
                        },
                        {
                            "component_id": "spy-settlement",
                            "component_name": "SPY official close",
                            "component_type": "benchmark",
                            "coefficient": -0.45,
                            "source_currency": "USD",
                            "price_convention": "unadjusted_official_close",
                        },
                    ),
                )
                studio = refresh_fixed_unit_composite(
                    connection,
                    studio_definition,
                    generated_at="2025-01-21T16:00:02+08:00",
                )
                self.assertEqual(2, studio["nav_dates"])
                self.assertAlmostEqual(103.66, studio["latest_nav"], places=6)
                stored = connection.execute(
                    """
                    SELECT exclusion_rule_json, calculation_version
                    FROM derived_portfolios WHERE portfolio_id = 'studio-alpha-copy'
                    """
                ).fetchone()
                self.assertEqual(
                    "fixed-unit-composite-studio-v1", stored["calculation_version"]
                )
                self.assertIn('"subject_id":"studio-alpha-copy"', stored["exclusion_rule_json"])
                # Invalid source rows must fail before any derived writes, not
                # silently shrink the common calendar and publish stale history.
                connection.execute("UPDATE nav_snapshots SET total_text='nan' WHERE account_id='U16088606' AND snapshot_date='2025-01-21'")
                connection.commit()
                with self.assertRaisesRegex(ValueError, 'invalid observations'):
                    refresh_alpha_world_t_portfolio(connection)
                self.assertEqual(t_before, [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t'")])
                connection.execute("UPDATE nav_snapshots SET total_text='360' WHERE account_id='U16088606' AND snapshot_date='2025-01-21'")
                connection.execute("UPDATE nav_snapshots SET snapshot_date='2025-01-27' WHERE snapshot_date='2025-01-21'")
                connection.execute("DELETE FROM fx_rates WHERE rate_date='2025-01-21'")
                connection.commit()
                with self.assertRaisesRegex(ValueError, 'four-calendar-day carry'):
                    refresh_alpha_world_t_portfolio(connection)
                self.assertEqual(t_before, [tuple(r) for r in connection.execute(
                    "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t'")])
            finally:
                connection.close()

    def test_refresh_excludes_taiwan_without_double_counting_all_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "portfolio.db"
            (root / "reference_data.json").write_text(
                json.dumps({"version": 2, "sectors": {}, "risk_free": {}}), encoding="utf-8"
            )
            initialize(database)
            connection = connect(database)
            try:
                connection.execute(
                    """
                    INSERT INTO ingestion_runs(
                        run_type, requested_from, requested_to, started_at,
                        completed_at, status, row_count, reconciliation_json
                    ) VALUES ('test', '2026-08-25', '2026-08-26',
                              '2026-08-27T08:00:00Z', '2026-08-27T08:01:00Z',
                              'success', 10, '{"status":"PASS"}')
                    """
                )
                run_id = connection.execute(
                    "SELECT run_id FROM ingestion_runs"
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO accounts(
                        account_id, alias, base_currency, account_type, ib_entity,
                        first_seen_at, last_seen_at, source_json
                    ) VALUES ('U16088606', 'Source', 'USD', 'Individual', 'IBHK',
                              '2026-08-25', '2026-08-26', '{}')
                    """
                )
                instruments = (
                    (
                        "TW1", "2330", "TAIWAN SEMICONDUCTOR", "STK", "TWD",
                        "TWSE", "2026-08-25", "2026-08-26",
                    ),
                    (
                        "HK1", "700", "TENCENT", "STK", "USD",
                        "SEHK", "2026-08-25", "2026-08-26",
                    ),
                )
                for row in instruments:
                    connection.execute(
                        """
                        INSERT INTO instruments(
                            conid, symbol, description, asset_category, currency,
                            listing_exchange, first_seen_at, last_seen_at, source_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
                        """,
                        row,
                    )
                for snapshot_date, source_total, taiwan_value, included_value in (
                    ("2026-08-25", 200.0, 1000.0, 120.0),
                    ("2026-08-26", 210.0, 1100.0, 127.0),
                ):
                    connection.execute(
                        """
                        INSERT INTO nav_snapshots(
                            account_id, snapshot_date, currency, asset_class,
                            total_text, cash_text, stock_text, options_text,
                            futures_text, bonds_text, accruals_text, row_sha256,
                            last_seen_run_id
                        ) VALUES ('U16088606', ?, 'USD', 'TOTAL', ?, '50', ?,
                                  '0', '0', '0', '0', ?, ?)
                        """,
                        (
                            snapshot_date,
                            str(source_total),
                            str(source_total - 50),
                            f"nav-{snapshot_date}",
                            run_id,
                        ),
                    )
                    for conid, currency, value in (
                        ("TW1", "TWD", taiwan_value),
                        ("HK1", "USD", included_value),
                    ):
                        connection.execute(
                            """
                            INSERT INTO position_snapshots(
                                account_id, snapshot_date, conid, model_code,
                                currency, quantity_text, cost_basis_text,
                                mark_price_text, market_value_text,
                                unrealized_pnl_text, row_sha256, last_seen_run_id
                            ) VALUES ('U16088606', ?, ?, '', ?, '1', ?, ?, ?,
                                      '0', ?, ?)
                            """,
                            (
                                snapshot_date,
                                conid,
                                currency,
                                str(value),
                                str(value),
                                str(value),
                                f"position-{snapshot_date}-{conid}",
                                run_id,
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO fx_rates(
                            account_id, rate_date, from_currency, to_currency,
                            rate_text, row_sha256, last_seen_run_id
                        ) VALUES ('U16088606', ?, 'TWD', 'USD', '0.03', ?, ?)
                        """,
                        (snapshot_date, f"fx-{snapshot_date}", run_id),
                    )
                connection.execute(
                    """
                    INSERT INTO fx_rates(
                        account_id, rate_date, from_currency, to_currency,
                        rate_text, row_sha256, last_seen_run_id
                    ) VALUES ('U16088606', '2025-01-20', 'HKD', 'USD',
                              '0.13', 'fx-hkd-opening', ?)
                    """,
                    (run_id,),
                )
                cash_rows = (
                    ("cash-hkd", "20260826;120000", "2026-08-26", "HK1", "100", "HKD", "HK dividend"),
                    ("cash-hkd-duplicate", "20260826", "2026-08-26", "HK1", "100", "HKD", "HK dividend"),
                    ("cash-twd", "20260826;130000", "2026-08-26", None, "-10", "TWD", "TWD custody fee"),
                    ("cash-taiwan-usd", "20260826;140000", "2026-08-26", "TW1", "20", "USD", "Taiwan dividend"),
                    ("cash-usd-interest", "20260826;150000", "2026-08-26", None, "5", "USD", "USD CREDIT INT"),
                )
                for source_key, event_datetime, effective_date, conid, amount, currency, description in cash_rows:
                    connection.execute(
                        """
                        INSERT INTO cash_transactions(
                            account_id, source_key, transaction_id, event_datetime,
                            model_code, effective_date, settle_date, transaction_type,
                            description, amount_text, currency, conid, row_sha256,
                            last_seen_run_id
                        ) VALUES ('U16088606', ?, '', ?, '', ?, ?, '', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_key,
                            event_datetime,
                            effective_date,
                            effective_date,
                            description,
                            amount,
                            currency,
                            conid,
                            f"row-{source_key}",
                            run_id,
                        ),
                    )
                connection.commit()

                result = refresh_hong_kong_china_portfolio(
                    connection,
                    root / "reference_data.json",
                    generated_at="2026-08-27T16:00:00+08:00",
                )
                self.assertEqual(1, result["latest_excluded_positions"])
                self.assertAlmostEqual(33.0, result["latest_excluded_market_value_base"])
                self.assertEqual("2025-01-20", result["history_start_date"])

                # A second refresh is idempotent and replaces the same derived rows.
                refresh_hong_kong_china_portfolio(
                    connection,
                    root / "reference_data.json",
                    generated_at="2026-08-27T16:01:00+08:00",
                )
                latest = connection.execute(
                    """
                    SELECT total_nav, stock, cash, included_position_count,
                           excluded_position_count
                    FROM derived_portfolio_nav
                    WHERE portfolio_id = 'hong-kong-china'
                      AND snapshot_date = '2026-08-26'
                    """
                ).fetchone()
                self.assertAlmostEqual(63104.0, latest["total_nav"])
                self.assertAlmostEqual(127.0, latest["stock"])
                self.assertAlmostEqual(62977.0, latest["cash"])
                self.assertEqual(1, latest["included_position_count"])
                self.assertEqual(1, latest["excluded_position_count"])
                self.assertEqual(
                    0,
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM derived_portfolio_cash_ledger
                        WHERE portfolio_id = 'hong-kong-china'
                          AND included = 1 AND currency NOT IN ('HKD', 'USD')
                        """
                    ).fetchone()[0],
                )
                balances = {
                    row["currency"]: row["closing_balance"]
                    for row in connection.execute(
                        """
                        SELECT currency, closing_balance
                        FROM derived_portfolio_cash_balances
                        WHERE portfolio_id = 'hong-kong-china'
                          AND snapshot_date = '2026-08-26'
                        """
                    )
                }
                self.assertAlmostEqual(484400.0, balances["HKD"])
                self.assertAlmostEqual(5.0, balances["USD"])
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM derived_portfolio_positions"
                    ).fetchone()[0],
                )

                payload = _report_payload(
                    connection,
                    datetime(
                        2026, 8, 27, 16, 0, tzinfo=timezone(timedelta(hours=8))
                    ),
                    {"risk_free": {}, "sectors": {}},
                )
                self.assertIn("hong-kong-china", payload["view_order"])
                self.assertAlmostEqual(
                    210.0, payload["views"]["__all__"]["metrics"]["nav"]
                )
                derived = payload["views"]["hong-kong-china"]
                self.assertAlmostEqual(63104.0, derived["metrics"]["nav"])
                self.assertEqual(1, derived["metrics"]["position_count"])
                self.assertEqual(1, derived["metrics"]["excluded_position_count"])
                self.assertEqual("700", derived["positions"][0]["symbol"])
                self.assertEqual(
                    {"HKD": 484400.0, "USD": 5.0},
                    derived["derived_portfolio"]["latest_cash_balances"],
                )

                studio_definition = filtered_portfolio_definition(
                    subject_id="studio-non-taiwan",
                    display_name="Studio Non-Taiwan",
                    source_account_id="U16088606",
                    base_currency="USD",
                    history_start_date="2025-01-20",
                    excluded_countries=("Taiwan",),
                    excluded_exchanges=("TWSE",),
                    excluded_currencies=("TWD",),
                    opening_cash={"USD": 100.0},
                    allowed_cash_currencies=("USD",),
                    include_actual_interest=False,
                )
                studio = refresh_filtered_portfolio(
                    connection,
                    root / "reference_data.json",
                    contract=studio_definition,
                    generated_at="2026-08-27T16:02:00+08:00",
                )
                self.assertEqual(1, studio["latest_excluded_positions"])
                studio_latest = connection.execute(
                    """
                    SELECT total_nav, cash, included_position_count,
                           excluded_position_count
                    FROM derived_portfolio_nav
                    WHERE portfolio_id = 'studio-non-taiwan'
                      AND snapshot_date = '2026-08-26'
                    """
                ).fetchone()
                self.assertAlmostEqual(227.0, studio_latest["total_nav"])
                self.assertAlmostEqual(100.0, studio_latest["cash"])
                self.assertEqual(1, studio_latest["included_position_count"])
                self.assertEqual(1, studio_latest["excluded_position_count"])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
