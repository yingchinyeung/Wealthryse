from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl.market_data import ALPHA_WORLD_MARKET_INSTRUMENTS, fetch_daily_closes

from ibkr_etl.reference_data import (
    _candidate_symbols,
    _parse_usd_risk_free_xml,
    load_reference_data,
    save_reference_data,
)


class ReferenceDataTests(unittest.TestCase):
    def test_market_close_parser_uses_exchange_date_and_unadjusted_close(self) -> None:
        timestamp = int(datetime(2025, 1, 20, 5, tzinfo=timezone.utc).timestamp())
        payload = {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {
                            "currency": "HKD",
                            "exchangeTimezoneName": "Asia/Hong_Kong",
                        },
                        "timestamp": [timestamp],
                        "indicators": {"quote": [{"close": [19862.33]}]},
                    }
                ],
            }
        }
        with patch("ibkr_etl.market_data._download_json", return_value=payload):
            _, rows = fetch_daily_closes(
                ALPHA_WORLD_MARKET_INSTRUMENTS[0],
                date(2025, 1, 20),
                date(2025, 1, 20),
            )
        self.assertEqual([("2025-01-20", 19862.33)], rows)

    def test_exchange_symbol_candidates(self) -> None:
        self.assertEqual(["0700.HK", "700"], _candidate_symbols("700", "SEHK", "HKD"))
        self.assertEqual(["7203.T", "7203"], _candidate_symbols("7203", "TSEJ", "JPY"))
        self.assertEqual(["AAPL"], _candidate_symbols("AAPL", "NASDAQ", "USD"))

    def test_reference_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "reference_data.json"
            payload = {
                "version": 1,
                "risk_free": {"USD": {"annual_rate": 0.04}},
                "sectors": {"1": {"sector": "Technology"}},
            }
            save_reference_data(path, payload)
            self.assertEqual(payload, load_reference_data(path))

    def test_parses_current_treasury_xml_format(self) -> None:
        xml = b"""<QR_BC_CM><G_NEW_DATE><LIST_G_BC_CAT><G_BC_CAT>
        <BC_3MONTH>3.91</BC_3MONTH></G_BC_CAT></LIST_G_BC_CAT>
        <NEW_DATE>08-03-2026</NEW_DATE></G_NEW_DATE></QR_BC_CM>"""
        self.assertEqual(("2026-08-03", 3.91), _parse_usd_risk_free_xml(xml))


if __name__ == "__main__":
    unittest.main()
