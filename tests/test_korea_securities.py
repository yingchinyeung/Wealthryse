"""Korean holdings print Yahoo's English names and the Korea iStrategy sectors, or stop the render."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import monthly_artifacts as legacy  # noqa: E402
from ibkr_etl.factsheet_spec import stress  # noqa: E402
from ibkr_etl.korea_securities import INDUSTRIES  # noqa: E402

# What Yahoo returned on 20 Oct 2026 for every KRX code the account has held: (longName, sector, industry).
YAHOO = json.loads(Path(__file__).with_name("fixtures").joinpath("krx_yahoo_2026-10-20.json").read_text())

# The 31 Aug 2026 holdings, by KRX code with approximate market values in won, and the owner's
# factsheet's sector table for that month (sub-sector: weight %).
AUG_31 = {
    "000660": 23996000, "005930": 12236000, "329180": 7828500, "020150": 4068750, "353200": 3465000,
    "086790": 3206200, "023530": 2717500, "012630": 2402400, "055550": 2346750, "000210": 2214500,
    "000270": 2148800, "001060": 1997550, "004000": 1958000, "036570": 1844000, "267250": 1792000,
    "027410": 1789900, "068270": 1742400, "035420": 1554000, "103140": 1339200, "034730": 1116000,
    "000720": 1004000, "018260": 735000, "005490": 692000, "017670": 579000, "015760": 487500,
}
AUG_31_SECTORS = {
    "Semiconductors & Semiconductor Equipment": 27.69, "Technology Hardware & Equipment": 18.38,
    "Machinery": 9.28, "Capital Goods": 8.74, "Materials": 7.86, "Banks": 6.61,
    "Pharmaceuticals, Biotechnology & Life Sciences": 4.56, "Retailing": 3.17,
    "Automobiles & Components": 2.54, "Entertainment": 2.17, "Energy": 2.12, "Media & Entertainment": 1.84,
    "Metals & Mining": 1.61, "Industrial Conglomerates": 1.31, "Software & Services": 0.87,
    "Telecommunication Services": 0.68, "Electric Utilities": 0.58,
}


def _korea_snapshot(values):
    snapshot = stress.snapshot(subject_id="KIWOOM-5057355911", currency="KRW", holdings=len(values))
    rows, classifications = [], []
    for i, (code, value) in enumerate(values.items()):
        conid = f"KRX:{code}"
        rows.append({"conid": conid, "symbol": code, "description": "한국어 이름", "currency": "KRW",
                     "market_value_text": f"{value:.2f}"})
        if code in YAHOO:
            name, sector, industry = YAHOO[code]
            classifications.append({"conid": conid, "symbol": code, "region": "Asia Pacific",
                                    "sector": sector, "sub_sector": industry, "long_name": name})
    snapshot["holdings"]["holdings"] = rows
    snapshot["sector_allocation"]["classifications"] = classifications
    snapshot["sector_allocation"]["gross_market_value"] = float(sum(values.values()))
    snapshot["portfolio_summary"]["positions"] = len(rows)
    return snapshot


class KoreaSecuritiesTests(unittest.TestCase):
    def test_english_names_south_korea_and_the_owners_sectors(self):
        inputs = legacy.factsheet_inputs(_korea_snapshot(AUG_31), reporting_currencies=("KRW",))
        self.assertEqual([h["company_name"] for h in inputs.top_holdings], [
            "SK hynix Inc.", "Samsung Electronics Co., Ltd.", "HD Hyundai Heavy Industries Co., Ltd.",
            "Lotte Energy Materials Corporation", "DAEDUCK ELECTRONICS Co., Ltd."])
        self.assertEqual({h["region"] for h in inputs.top_holdings}, {"South Korea"})
        self.assertEqual(inputs.top_holdings[0]["sector"], "Information Technology")
        printed = {row["sub_sector"]: row for row in inputs.sectors}
        self.assertEqual(set(printed), set(AUG_31_SECTORS))
        self.assertEqual({row["region"] for row in inputs.sectors}, {"South Korea"})

    def test_the_august_sector_table_has_the_owners_buckets_in_the_owners_order(self):
        inputs = legacy.factsheet_inputs(_korea_snapshot(AUG_31), reporting_currencies=("KRW",))
        order = [row["sub_sector"] for row in inputs.sectors]
        self.assertEqual(order, list(AUG_31_SECTORS))

    def test_a_code_with_no_yahoo_name_stops_the_render_and_names_it(self):
        snapshot = _korea_snapshot({"000660": 2000000, "005930": 1000000})
        snapshot["sector_allocation"]["classifications"][1].pop("long_name")
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "KRX code 005930"):
            legacy.factsheet_inputs(snapshot, reporting_currencies=("KRW",))

    def test_a_korean_name_from_yahoo_stops_the_render(self):
        snapshot = _korea_snapshot({"000660": 2000000})
        snapshot["sector_allocation"]["classifications"][0]["long_name"] = "SK하이닉스"
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "KRX code 000660"):
            legacy.factsheet_inputs(snapshot, reporting_currencies=("KRW",))

    def test_an_unmapped_yahoo_industry_stops_the_render_and_names_it(self):
        snapshot = _korea_snapshot({"000660": 2000000})
        snapshot["sector_allocation"]["classifications"][0]["sub_sector"] = "Shell Companies"
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "KRX code 000660, Yahoo industry Shell Companies"):
            legacy.factsheet_inputs(snapshot, reporting_currencies=("KRW",))

    def test_yahoo_search_dashes_map_like_the_profile(self):
        # Yahoo's search answered "Banks\u2014Regional" on 20 Oct 2026 where the profile says "Banks - Regional".
        snapshot = _korea_snapshot({"105560": 2000000, "000100": 1000000})
        snapshot["sector_allocation"]["classifications"][0]["sub_sector"] = "Banks\u2014Regional"
        snapshot["sector_allocation"]["classifications"][1]["sub_sector"] = "Drug Manufacturers\u2014Specialty & Generic"
        inputs = legacy.factsheet_inputs(snapshot, reporting_currencies=("KRW",))
        self.assertEqual([row["sub_sector"] for row in inputs.sectors],
                         ["Banks", "Pharmaceuticals, Biotechnology & Life Sciences"])

    def test_every_industry_yahoo_gave_the_account_is_mapped(self):
        for code, (name, sector, industry) in YAHOO.items():
            with self.subTest(code):
                self.assertIn(industry, INDUSTRIES)
                self.assertTrue(name.isascii() and name)

    def test_every_label_is_plain_ascii_so_the_factsheet_fonts_can_draw_it(self):
        for industry, labels in INDUSTRIES.items():
            for text in labels:
                self.assertTrue(text.isascii() and text.strip() == text and text)

    def test_other_currencies_are_untouched(self):
        inputs = legacy.factsheet_inputs(stress.snapshot(currency="USD"), reporting_currencies=("USD",))
        self.assertNotIn("South Korea", {h["region"] for h in inputs.top_holdings})


if __name__ == "__main__":
    unittest.main()
