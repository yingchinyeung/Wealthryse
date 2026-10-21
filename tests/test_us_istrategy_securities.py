"""U.S. iStrategy holdings print Yahoo's English names, United States and the owner's sectors, or stop the render."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import monthly_artifacts as legacy  # noqa: E402
from ibkr_etl.factsheet_spec import components, stress  # noqa: E402
from ibkr_etl.factsheet_spec.spec import load_design  # noqa: E402
from ibkr_etl.us_istrategy_securities import INDUSTRIES  # noqa: E402

# Ticker: (market value in USD, Yahoo long name, Yahoo industry).
HOLDINGS = {
    "IONQ": (2200.0, "IonQ, Inc.", "Computer Hardware"),
    "MU": (1900.0, "Micron Technology, Inc.", "Semiconductors"),
    "JPM": (1200.0, "JPMorgan Chase & Co.", "Banks - Diversified"),
    "RIOT": (900.0, "Riot Platforms, Inc.", "Capital Markets"),
    "NWSA": (700.0, "News Corporation", "Publishing"),
    "NFLX": (650.0, "Netflix, Inc.", "Entertainment"),
}


def _us_snapshot(values=HOLDINGS):
    snapshot = stress.snapshot(subject_id="KIWOOM-US-TEST", currency="USD", holdings=len(values))
    snapshot.pop("synthetic_data")
    rows, classifications = [], []
    for ticker, (value, name, industry) in values.items():
        conid = f"US:{ticker}"
        rows.append({"conid": conid, "symbol": ticker, "description": ticker, "currency": "USD",
                     "native_currency": "USD", "market_value_text": f"{value:.2f}"})
        classifications.append({"conid": conid, "symbol": ticker, "region": "North America",
                                "sector": "Yahoo sector", "sub_sector": industry, "long_name": name})
    snapshot["holdings"]["holdings"] = rows
    snapshot["sector_allocation"]["classifications"] = classifications
    snapshot["sector_allocation"]["gross_market_value"] = sum(v[0] for v in values.values())
    snapshot["portfolio_summary"]["positions"] = len(rows)
    return snapshot


def _inputs(snapshot):
    return components.inputs_builder("us_istrategy", ("USD", "KRW"))(snapshot)


class UsIstrategySecuritiesTests(unittest.TestCase):
    def test_the_us_design_reads_the_us_securities(self):
        self.assertEqual(load_design("us-istrategy")["inputs"], "us_istrategy")
        self.assertEqual(load_design("korea-istrategy")["inputs"], "account")

    def test_english_names_united_states_and_the_owners_sectors(self):
        inputs = _inputs(_us_snapshot())
        self.assertEqual([h["company_name"] for h in inputs.top_holdings], [
            "IonQ, Inc.", "Micron Technology, Inc.", "JPMorgan Chase & Co.", "Riot Platforms, Inc.",
            "News Corporation"])
        self.assertEqual({h["region"] for h in inputs.top_holdings}, {"United States"})
        self.assertEqual([(row["sector"], row["sub_sector"]) for row in inputs.sectors], [
            ("Information Technology", "Technology Hardware & Equipment"),
            ("Information Technology", "Semiconductors & Semiconductor Equipment"),
            ("Financials", "Banks"),
            ("Financials", "Diversified Financials"),
            ("Consumer Discretionary", "Media"),
            ("Telecommunication Services", "Media & Entertainment"),
        ])
        self.assertEqual({row["region"] for row in inputs.sectors}, {"United States"})

    def test_qxo_follows_yahoos_current_industry(self):
        snapshot = _us_snapshot({"QXO": (100.0, "QXO, Inc.", "Industrial Distribution")})
        row = _inputs(snapshot).sectors[0]
        self.assertEqual((row["sector"], row["sub_sector"]), ("Industrials", "Capital Goods"))

    def test_yahoo_dash_spellings_read_the_same(self):
        snapshot = _us_snapshot({"JPM": (100.0, "JPMorgan Chase & Co.", "Banks—Diversified")})
        self.assertEqual(_inputs(snapshot).sectors[0]["sub_sector"], "Banks")

    def test_a_ticker_with_no_yahoo_name_stops_the_render_and_names_it(self):
        snapshot = _us_snapshot()
        snapshot["sector_allocation"]["classifications"][2].pop("long_name")
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "English name stored for JPM"):
            _inputs(snapshot)

    def test_an_unknown_industry_stops_the_render_and_names_it(self):
        snapshot = _us_snapshot({"ZZZ": (100.0, "Unknown Co.", "Shell Companies")})
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "ZZZ, Yahoo industry Shell Companies"):
            _inputs(snapshot)

    def test_a_ticker_with_no_classification_stops_the_render(self):
        snapshot = _us_snapshot()
        snapshot["sector_allocation"]["classifications"].pop(0)
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "IONQ"):
            _inputs(snapshot)

    def test_a_holding_yahoo_stopped_quoting_after_a_takeover_keeps_its_listed_name(self):
        snapshot = _us_snapshot({"FYBR": (300.0, "", ""), "DFS": (200.0, "", "")})
        for row in snapshot["sector_allocation"]["classifications"]:
            row.pop("long_name"), row.pop("sub_sector")
        inputs = _inputs(snapshot)
        self.assertEqual([h["company_name"] for h in inputs.top_holdings], [
            "Frontier Communications Parent, Inc.", "Discover Financial Services"])
        self.assertEqual([(row["sector"], row["sub_sector"]) for row in inputs.sectors], [
            ("Telecommunication Services", "Telecommunication Services"), ("Financials", "Diversified Financials")])

    def test_a_delisted_holding_with_no_classification_row_still_renders(self):
        snapshot = _us_snapshot({"DFS": (200.0, "", ""), "MU": (100.0, "Micron Technology, Inc.", "Semiconductors")})
        snapshot["sector_allocation"]["classifications"].pop(0)
        self.assertEqual(_inputs(snapshot).top_holdings[0]["company_name"], "Discover Financial Services")

    def test_designer_previews_keep_their_made_up_holdings(self):
        snapshot = stress.snapshot(currency="USD")
        inputs = _inputs(snapshot)
        self.assertEqual(inputs.sectors[0]["sector"], stress.snapshot(currency="USD")[
            "sector_allocation"]["classifications"][0]["sector"])

    def test_the_account_design_still_prints_yahoos_own_labels(self):
        inputs = legacy.factsheet_inputs(_us_snapshot(), reporting_currencies=("USD",))
        self.assertEqual(inputs.top_holdings[0]["region"], "North America")

    def test_every_mapped_sub_sector_is_one_the_factsheet_prints(self):
        sectors = {sector for sector, _ in INDUSTRIES.values()}
        self.assertLessEqual(sectors, {
            "Information Technology", "Industrials", "Financials", "Health Care", "Consumer Discretionary",
            "Consumer Staples", "Telecommunication Services", "Energy", "Utilities", "Materials"})


if __name__ == "__main__":
    unittest.main()
