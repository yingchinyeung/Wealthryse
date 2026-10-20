from __future__ import annotations

import unittest
import sqlite3

from ibkr_etl.sector_classification import (
    aggregate_sector_allocation,
    candidate_symbols,
    lookup_classification,
    refresh_sector_classifications_range,
)


class SectorClassificationTests(unittest.TestCase):
    def test_ibkr_bookkeeping_suffixes_use_underlying_yahoo_symbol(self) -> None:
        self.assertEqual(
            candidate_symbols("2885.REC", "TWSE", "TWD")[:2],
            ["2885.TW", "2885.TWO"],
        )
        self.assertEqual(
            candidate_symbols("2888.OLD", "TWSE", "TWD")[:2],
            ["2888.TW", "2888.TWO"],
        )

    def test_candidate_symbols_normalize_us_and_hong_kong_listings(self) -> None:
        self.assertEqual(candidate_symbols("BRK B", "NYSE", "USD"), ["BRK-B"])
        self.assertEqual(candidate_symbols("700", "SEHK", "HKD"), ["0700.HK", "700"])

    def test_lookup_accepts_only_an_exact_candidate_with_sector(self) -> None:
        def fetcher(symbol: str, timeout: int):
            self.assertEqual(symbol, "AAPL")
            self.assertEqual(timeout, 10)
            return {
                "quotes": [
                    {"symbol": "AAPL.MX", "sector": "Technology"},
                    {
                        "symbol": "AAPL",
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "exchange": "NMS",
                    },
                ]
            }

        result = lookup_classification(
            {"conid": "265598", "symbol": "AAPL", "currency": "USD"},
            timeout=10,
            fetcher=fetcher,
        )
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["lookup_symbol"], "AAPL")
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["license_status"], "restricted")

    def test_lookup_uses_profile_when_search_has_no_sector(self) -> None:
        result = lookup_classification(
            {"conid": "15020108", "symbol": "VRNT", "currency": "USD"},
            fetcher=lambda symbol, timeout: {"quotes": []},
            profile_fetcher=lambda symbol: {
                "symbol": "VRNT",
                "sector": "Technology",
                "industry": "Software - Infrastructure",
                "exchange": "NMS",
            },
        )
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["sector"], "Technology")
        self.assertEqual(result["error_code"], "yfinance_profile_fallback")

    def test_historical_override_is_bound_to_the_exact_ibkr_conid(self) -> None:
        result = lookup_classification(
            {"conid": "10814", "symbol": "OMI", "currency": "USD"},
            fetcher=lambda symbol, timeout: {"quotes": []},
            profile_fetcher=lambda symbol: {},
        )
        self.assertEqual(result["sector"], "Healthcare")
        self.assertEqual(result["industry"], "Medical Distribution")
        self.assertIn("sec.gov", result["source_url"])
        self.assertEqual(
            result["error_code"], "verified_historical_override:sec-2024-10-k"
        )

        taiwan_result = lookup_classification(
            {
                "conid": "38768970",
                "symbol": "2888.OLD",
                "listing_exchange": "TWSE",
                "currency": "TWD",
            },
            fetcher=lambda symbol, timeout: {"quotes": []},
            profile_fetcher=lambda symbol: {},
        )
        self.assertEqual(taiwan_result["sector"], "Financial Services")
        self.assertEqual(taiwan_result["industry"], "Finance and Insurance")
        self.assertIn("twse.com.tw", taiwan_result["source_url"])
        self.assertEqual(taiwan_result["conid"], "38768970")
        self.assertEqual(
            taiwan_result["error_code"],
            "verified_historical_override:twse-delisting-2025-06-27",
        )

    def test_new_warrant_override_uses_underlying_sector_until_yahoo_has_one(
        self,
    ) -> None:
        holding = {
            "conid": "931131905",
            "symbol": "SKYD WS",
            "listing_exchange": "NYSE",
            "currency": "USD",
        }
        result = lookup_classification(
            holding,
            fetcher=lambda symbol, timeout: {"quotes": []},
            profile_fetcher=lambda symbol: {},
        )
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["sector"], "Communication Services")
        self.assertEqual(result["symbol"], "SKYD WS")
        self.assertEqual(
            result["error_code"],
            "owner_approved_underlying_override:occ-memo-59906",
        )

        yahoo_result = lookup_classification(
            holding,
            fetcher=lambda symbol, timeout: {
                "quotes": [
                    {"symbol": "SKYD-WS", "sector": "Technology", "industry": "X"}
                ]
            },
            profile_fetcher=lambda symbol: {},
        )
        self.assertEqual(yahoo_result["sector"], "Technology")
        self.assertIsNone(yahoo_result["error_code"])

    def test_allocation_uses_absolute_market_value_and_reports_coverage(self) -> None:
        result = aggregate_sector_allocation(
            [
                {
                    "conid": "1",
                    "symbol": "A",
                    "market_value_text": "60",
                    "currency": "USD",
                    "sector": "Technology",
                    "industry": "Software",
                    "checked_at": "2026-09-23",
                },
                {
                    "conid": "2",
                    "symbol": "B",
                    "market_value_text": "-40",
                    "currency": "USD",
                    "sector": "Healthcare",
                    "industry": "Biotechnology",
                    "checked_at": "2026-09-23",
                },
            ]
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["classification_coverage_pct"], 100.0)
        self.assertEqual(result["allocation"][0]["weight"], 0.6)
        self.assertEqual(result["allocation_detail"][0]["region"], "North America")
        self.assertEqual(result["allocation_detail"][0]["sub_sector"], "Software")
        self.assertEqual(result["classifications"][0]["weight"], 0.6)
        self.assertFalse(result["publication_allowed"])

    def test_missing_classification_fails_closed(self) -> None:
        result = aggregate_sector_allocation(
            [{"symbol": "UNKNOWN", "market_value_text": "25", "sector": None}]
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["unclassified_symbols"], ["UNKNOWN"])

    def test_historical_range_only_transmits_unclassified_holdings(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE instruments(
                conid TEXT PRIMARY KEY, symbol TEXT, listing_exchange TEXT,
                currency TEXT
            );
            CREATE TABLE position_snapshots(
                account_id TEXT, snapshot_date TEXT, conid TEXT,
                currency TEXT, quantity_text TEXT
            );
            CREATE TABLE instrument_classifications(
                tenant_id TEXT NOT NULL DEFAULT 'tenant-1', conid TEXT NOT NULL,
                provider TEXT NOT NULL, symbol TEXT NOT NULL, lookup_symbol TEXT,
                sector TEXT, industry TEXT, exchange TEXT, status TEXT NOT NULL,
                quality_status TEXT NOT NULL, license_status TEXT NOT NULL,
                source_url TEXT NOT NULL, response_sha256 TEXT, error_code TEXT,
                checked_at TEXT NOT NULL,
                UNIQUE(tenant_id, conid, provider)
            );
            INSERT INTO instruments VALUES ('1', 'AAPL', 'NASDAQ', 'USD');
            INSERT INTO instruments VALUES ('2', 'MSFT', 'NASDAQ', 'USD');
            INSERT INTO position_snapshots VALUES ('U1', '2025-01-31', '1', 'USD', '1');
            INSERT INTO position_snapshots VALUES ('U1', '2025-02-28', '2', 'USD', '1');
            INSERT INTO instrument_classifications VALUES (
                'tenant-1', '1', 'Yahoo Finance search profile', 'AAPL', 'AAPL',
                'Technology', '', 'NMS', 'classified', 'warn', 'restricted',
                'test', 'digest', NULL, '2026-09-23'
            );
            """
        )
        transmitted: list[str] = []

        def fetcher(symbol: str, timeout: int):
            transmitted.append(symbol)
            return {
                "quotes": [
                    {
                        "symbol": symbol,
                        "sector": "Technology",
                        "industry": "Software",
                        "exchange": "NMS",
                    }
                ]
            }

        try:
            result = refresh_sector_classifications_range(
                connection,
                subject_id="U1",
                start_date="2025-01-20",
                end_date="2025-02-28",
                workers=1,
                fetcher=fetcher,
            )
        finally:
            connection.close()
        self.assertEqual(result["historical_unique_holdings"], 2)
        self.assertEqual(result["previously_classified"], 1)
        self.assertEqual(result["tickers_transmitted"], 1)
        self.assertEqual(transmitted, ["MSFT"])


    def _krw_database(self, *, with_name_column: bool):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE instruments(conid TEXT PRIMARY KEY, symbol TEXT, listing_exchange TEXT, currency TEXT);
            CREATE TABLE position_snapshots(account_id TEXT, snapshot_date TEXT, conid TEXT, currency TEXT,
                                            quantity_text TEXT);
            CREATE TABLE instrument_classifications(
                tenant_id TEXT NOT NULL DEFAULT 'tenant-1', conid TEXT NOT NULL,
                provider TEXT NOT NULL, symbol TEXT NOT NULL, lookup_symbol TEXT,
                sector TEXT, industry TEXT, exchange TEXT, status TEXT NOT NULL,
                quality_status TEXT NOT NULL, license_status TEXT NOT NULL,
                source_url TEXT NOT NULL, response_sha256 TEXT, error_code TEXT,
                checked_at TEXT NOT NULL,
                UNIQUE(tenant_id, conid, provider)
            );
            INSERT INTO instruments VALUES ('KRX:000660', '000660', 'KRX', 'KRW');
            INSERT INTO instruments VALUES ('1', 'AAPL', 'NASDAQ', 'USD');
            INSERT INTO position_snapshots VALUES ('K1', '2026-08-31', 'KRX:000660', 'KRW', '14');
            INSERT INTO position_snapshots VALUES ('K1', '2026-08-31', '1', 'USD', '1');
            INSERT INTO instrument_classifications VALUES (
                'tenant-1', 'KRX:000660', 'Yahoo Finance search profile', '000660', '000660.KS',
                'Technology', 'Semiconductors', 'KSC', 'classified', 'warn', 'restricted', 'test', 'd', NULL, '2026-10-01');
            INSERT INTO instrument_classifications VALUES (
                'tenant-1', '1', 'Yahoo Finance search profile', 'AAPL', 'AAPL',
                'Technology', 'Consumer Electronics', 'NMS', 'classified', 'warn', 'restricted', 'test', 'd', NULL,
                '2026-10-01');
            """
        )
        if with_name_column:
            connection.execute("ALTER TABLE instrument_classifications ADD COLUMN long_name TEXT")
        return connection

    @staticmethod
    def _fetcher(transmitted):
        def fetch(symbol: str, timeout: int):
            transmitted.append(symbol)
            return {"quotes": [{"symbol": symbol, "sector": "Technology", "industry": "Semiconductors",
                                "exchange": "KSC", "longname": "SK hynix Inc.", "shortname": "SK HYNIX"}]}
        return fetch

    def test_a_krw_holding_without_its_english_name_is_looked_up_again_and_stores_it(self) -> None:
        connection = self._krw_database(with_name_column=True)
        transmitted: list[str] = []
        try:
            result = refresh_sector_classifications_range(
                connection, subject_id="K1", start_date="2026-08-01", end_date="2026-08-31", workers=1,
                fetcher=self._fetcher(transmitted))
            stored = connection.execute(
                "SELECT long_name FROM instrument_classifications WHERE conid = 'KRX:000660'").fetchone()
            again: list[str] = []
            refresh_sector_classifications_range(
                connection, subject_id="K1", start_date="2026-08-01", end_date="2026-08-31", workers=1,
                fetcher=self._fetcher(again))
        finally:
            connection.close()
        self.assertEqual(transmitted, ["000660.KS"])  # the USD holding with a sector is left alone
        self.assertEqual(result["previously_classified"], 1)
        self.assertEqual(stored["long_name"], "SK hynix Inc.")
        self.assertEqual(again, [])  # named now, so not looked up again

    def test_before_migration_080_nothing_changes(self) -> None:
        connection = self._krw_database(with_name_column=False)
        transmitted: list[str] = []
        try:
            result = refresh_sector_classifications_range(
                connection, subject_id="K1", start_date="2026-08-01", end_date="2026-08-31", workers=1,
                fetcher=self._fetcher(transmitted))
        finally:
            connection.close()
        self.assertEqual(transmitted, [])
        self.assertEqual(result["previously_classified"], 2)

    def test_the_allocation_carries_the_english_name(self) -> None:
        result = aggregate_sector_allocation([
            {"conid": "KRX:000660", "symbol": "000660", "currency": "KRW", "market_value_text": "10",
             "sector": "Technology", "industry": "Semiconductors", "long_name": "SK hynix Inc."},
            {"conid": "1", "symbol": "AAPL", "currency": "USD", "market_value_text": "5",
             "sector": "Technology", "industry": "Consumer Electronics"}])
        names = {row["conid"]: row.get("long_name") for row in result["classifications"]}
        self.assertEqual(names, {"KRX:000660": "SK hynix Inc.", "1": None})


if __name__ == "__main__":
    unittest.main()
