import unittest
from ibkr_etl.monthly_artifacts import _specific_region

class ReportingCurrencyRegionTests(unittest.TestCase):
    def test_native_listing_currency_survives_usd_valuation(self):
        for currency, symbol, expected in [('HKD','763','Hong Kong'),('TWD','2330','Taiwan'),('CNY','600000','China'),('USD','AAPL','North America')]:
            with self.subTest(currency=currency):
                self.assertEqual(_specific_region({'currency':'USD','native_currency':currency,'symbol':symbol},{}),expected)

    def test_unconverted_and_composite_conventions_unchanged(self):
        self.assertEqual(_specific_region({'currency':'HKD'},{}),'Hong Kong')
        self.assertEqual(_specific_region({'currency':'USD','symbol':'2330.TW'},{}),'Taiwan')
        self.assertEqual(_specific_region({'currency':'USD','native_currency':'HKD','composite_sleeve':True},{}),'Multi-asset')
