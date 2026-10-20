"""Korean holdings print their English names and the Korea iStrategy sectors, or stop the render."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl import monthly_artifacts as legacy  # noqa: E402
from ibkr_etl.factsheet_spec import stress  # noqa: E402
from ibkr_etl.korea_securities import SECURITIES  # noqa: E402

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
        classifications.append({"conid": conid, "symbol": code, "region": "Asia Pacific",
                                "sector": "Technology", "sub_sector": "Semiconductors"})
    snapshot["holdings"]["holdings"] = rows
    snapshot["sector_allocation"]["classifications"] = classifications
    snapshot["sector_allocation"]["gross_market_value"] = float(sum(values.values()))
    snapshot["portfolio_summary"]["positions"] = len(rows)
    return snapshot


class KoreaSecuritiesTests(unittest.TestCase):
    def test_english_names_south_korea_and_the_owners_sectors(self):
        inputs = legacy.factsheet_inputs(_korea_snapshot(AUG_31), reporting_currencies=("KRW",))
        self.assertEqual([h["company_name"] for h in inputs.top_holdings], [
            "SK hynix Inc.", "Samsung Electronics Co., Ltd.", "HD Hyundai Heavy Industries Co.,Ltd.",
            "Iljin Materials Co Ltd", "DAEDUCK ELECTRONICS Co., Ltd."])
        self.assertEqual({h["region"] for h in inputs.top_holdings}, {"South Korea"})
        self.assertEqual(inputs.top_holdings[0]["sector"], "Information Technology")
        printed = {row["sub_sector"]: row for row in inputs.sectors}
        self.assertEqual(set(printed), set(AUG_31_SECTORS))
        self.assertEqual({row["region"] for row in inputs.sectors}, {"South Korea"})

    def test_the_august_sector_table_has_the_owners_buckets_in_the_owners_order(self):
        inputs = legacy.factsheet_inputs(_korea_snapshot(AUG_31), reporting_currencies=("KRW",))
        order = [row["sub_sector"] for row in inputs.sectors]
        self.assertEqual(order, list(AUG_31_SECTORS))

    def test_an_unknown_krx_code_stops_the_render_and_names_it(self):
        with self.assertRaisesRegex(legacy.MonthlyArtifactError, "KRX code 999999"):
            legacy.factsheet_inputs(_korea_snapshot({"999999": 1000000, "000660": 2000000}),
                                    reporting_currencies=("KRW",))

    def test_every_name_is_plain_ascii_so_the_factsheet_fonts_can_draw_it(self):
        for code, (name, sector, sub_sector) in SECURITIES.items():
            with self.subTest(code):
                self.assertEqual(len(code), 6)
                for text in (name, sector, sub_sector):
                    self.assertTrue(text.isascii() and text.strip() == text and text)

    def test_other_currencies_are_untouched(self):
        inputs = legacy.factsheet_inputs(stress.snapshot(currency="USD"), reporting_currencies=("USD",))
        self.assertNotIn("South Korea", {h["region"] for h in inputs.top_holdings})


if __name__ == "__main__":
    unittest.main()
