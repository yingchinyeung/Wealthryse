"""Regression protection against loss of parsed currency-pair evidence."""
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from ibkr_etl.config import Settings
from ibkr_etl.db import connect
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.service import ingest_content


def document(rows, *, account="A", end="20250120", kind="ConversionRate"):
    statement = ET.Element("FlexStatement", accountId=account, toDate=end)
    section = ET.SubElement(statement, "ConversionRates")
    for attrs in rows:
        ET.SubElement(section, kind, attrs)
    return ET.tostring(statement)


def fx_rows(content):
    return [r for r in parse_flex_xml(content).rows if r.section == "ConversionRates"]


class FxSourceKeysTests(unittest.TestCase):
    def test_each_pair_has_its_own_key_for_all_supported_types(self):
        for kind in ("ConversionRate", "FxRate", "CurrencyConversionRate"):
            with self.subTest(kind=kind):
                rows = fx_rows(document([
                    {"fromCurrency": "PHP", "toCurrency": "USD", "rate": "0.017", "detailLevel": "DETAIL"},
                    {"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.128", "detailLevel": "DETAIL"},
                    {"fromCurrency": "PHP", "toCurrency": "HKD", "rate": "0.133", "detailLevel": "DETAIL"},
                ], kind=kind))
                self.assertEqual(len({r.source_key for r in rows}), 3)

    def test_correction_keeps_identity_but_changes_content_hash(self):
        before = fx_rows(document([{"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.128"}]))[0]
        after = fx_rows(document([{"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.129"}]))[0]
        self.assertEqual(before.source_key, after.source_key)
        self.assertNotEqual(before.row_sha256, after.row_sha256)

    def test_dates_accounts_models_are_separate(self):
        attrs = {"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.128"}
        records = [fx_rows(document([attrs]))[0],
                   fx_rows(document([attrs], end="20250121"))[0],
                   fx_rows(document([attrs], account="B"))[0],
                   fx_rows(document([{**attrs, "model": "sleeve"}]))[0]]
        self.assertEqual(len({r.source_key for r in records}), 4)

    def test_effective_date_and_currency_aliases_match_normalization(self):
        a = {"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.128", "reportDate": "20250120"}
        b = {"currency": "HKD", "baseCurrency": "USD", "exchangeRate": "0.128", "effectiveDate": "2025-01-20"}
        c = {"currency": "HKD", "baseCurrency": "USD", "fxRateToBase": "0.128"}
        rows = fx_rows(document([a, b, c]))
        self.assertEqual(len({r.source_key for r in rows}), 1)
        self.assertEqual({r.report_date for r in rows}, {"2025-01-20"})

    def test_unresolved_observations_preserve_distinct_content(self):
        rows = fx_rows(document([{"rate": "1"}, {"rate": "2"}]))
        self.assertEqual(len({r.source_key for r in rows}), 2)
        self.assertTrue(all(r.source_key.startswith("fx-rate-unresolved:v2:") for r in rows))

    def test_database_retains_both_pairs_and_idempotent_correction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(project_root=root, flex_token="", flex_query_id="",
                database_path=root / "portfolio.db", raw_report_dir=root / "raw", log_dir=root / "logs",
                timezone_name="Asia/Hong_Kong", daily_lookback_days=7, user_agent="test",
                request_timeout_seconds=10, max_wait_seconds=20)
            rows = [{"fromCurrency": "PHP", "toCurrency": "USD", "rate": "0.017"},
                    {"fromCurrency": "HKD", "toCurrency": "USD", "rate": "0.128"}]
            ingest_content(settings, document(rows), run_type="test")
            rows[0]["rate"] = "0.018"
            ingest_content(settings, document(rows), run_type="test")
            ingest_content(settings, document(rows), run_type="test")
            db = connect(settings.database_path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM flex_rows WHERE record_type='ConversionRate'").fetchone()[0], 2)
                values = dict(db.execute("SELECT from_currency,rate_text FROM fx_rates").fetchall())
                self.assertEqual(values, {"PHP": "0.018", "HKD": "0.128"})
            finally:
                db.close()
