from __future__ import annotations

import unittest
from pathlib import Path

from ibkr_etl.flex_recipe import RECIPE_SECTIONS, RECIPE_STEPS, check_flex_recipe

FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"

FULL = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="WealthRyse daily" type="AF">
 <FlexStatements count="2">
  <FlexStatement accountId="U0000001" fromDate="20260901" toDate="20260903">
   <AccountInformation accountId="U0000001" currency="USD" name="Synthetic" />
   <EquitySummaryInBase>
    <EquitySummaryByReportDateInBase reportDate="20260901" total="100" />
    <EquitySummaryByReportDateInBase reportDate="20260902" total="101" />
   </EquitySummaryInBase>
   <OpenPositions>
    <OpenPosition conid="1" position="1" markPrice="1" positionValue="1" currency="USD" />
   </OpenPositions>
   <CashReport><CashReportCurrency currency="USD" endingCash="5" /></CashReport>
   <SecuritiesInfo><SecurityInfo conid="1" isin="" /></SecuritiesInfo>
   <ConversionRates><ConversionRate fromCurrency="HKD" toCurrency="USD" rate="0.128" /></ConversionRates>
   <Trades />
   <CashTransactions />
   <Transfers />
   <CorporateActions />
  </FlexStatement>
  <FlexStatement accountId="U0000002" fromDate="2026-09-01" toDate="2026-09-03">
   <AccountInformation accountId="U0000002" currency="USD" />
   <OpenPositions><OpenPosition conid="2" position="1" currency="USD" /></OpenPositions>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>"""


def _by_section(findings):
    return {item.section: item for item in findings}


class FlexRecipeTests(unittest.TestCase):
    def test_a_statement_built_from_the_recipe_passes(self) -> None:
        result = check_flex_recipe(FULL)
        self.assertEqual(result.accounts, ("U0000001", "U0000002"))
        first = _by_section(result.account_findings["U0000001"])
        self.assertEqual({item.status for item in first.values()}, {"pass"})
        self.assertEqual(first["trades"].message, "Ticked; no records in this period.")
        self.assertEqual(first["nav"].message, "2 records.")

    def test_each_account_shows_what_is_missing(self) -> None:
        second = _by_section(check_flex_recipe(FULL).account_findings["U0000002"])
        self.assertEqual(second["dates"].status, "fail")
        self.assertEqual(second["nav"].status, "fail")
        self.assertIn("Tick Net Asset Value (NAV) in Base", second["nav"].message)
        self.assertEqual(second["positions"].status, "fail")
        self.assertIn("markPrice", second["positions"].message)
        self.assertEqual(second["trades"].status, "warn")
        self.assertEqual(check_flex_recipe(FULL).status, "fail")

    def test_existing_sample_statement_is_read_with_its_own_names(self) -> None:
        result = check_flex_recipe(FIXTURE.read_bytes())
        findings = _by_section(result.account_findings["U1234567"])
        self.assertEqual(findings["securities"].status, "pass")
        self.assertEqual(findings["fx"].status, "pass")
        self.assertEqual(findings["nav_daily"].status, "warn")
        self.assertEqual(findings["transfers"].status, "warn")
        self.assertEqual(findings["corporate_actions"].status, "warn")

    def test_wrong_query_type_and_error_responses_are_refused(self) -> None:
        trade_confirmations = FULL.replace(b'type="AF"', b'type="TCF"')
        query = _by_section(check_flex_recipe(trade_confirmations).statement_findings)["query"]
        self.assertEqual(query.status, "fail")
        with self.assertRaisesRegex(ValueError, "error instead"):
            check_flex_recipe(b"<FlexStatementResponse><ErrorCode>1020</ErrorCode></FlexStatementResponse>")
        with self.assertRaisesRegex(ValueError, "not valid XML"):
            check_flex_recipe(b"<FlexQueryResponse")
        with self.assertRaisesRegex(ValueError, "no accounts"):
            check_flex_recipe(b'<FlexQueryResponse type="AF"><FlexStatements /></FlexQueryResponse>')

    def test_recipe_steps_name_every_section(self) -> None:
        text = " ".join(RECIPE_STEPS)
        for section in RECIPE_SECTIONS:
            self.assertIn(section.label, text)
        self.assertIn("yyyyMMdd", text)
        self.assertIn("Paste it only into this form", text)


if __name__ == "__main__":
    unittest.main()
