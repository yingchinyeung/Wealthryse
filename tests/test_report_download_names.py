"""Download names drop the content-address prefix of stored report files."""
import unittest
from unittest import mock

from ibkr_etl import web

SHA_A = "616e74dfdc2da7fc75c711f20de4ad5af73bb6e00f32f9e37e4a6547fbc898d9"
SHA_B = "19e9d93f3aead918a46f9bd8a5929ad01bf4db1a706e28773128253e6a53bf99"


class ReportDownloadNameTests(unittest.TestCase):
    def name(self, filename, subject="U16088571"):
        return web._monthly_download_filename({"filename": filename, "report_type": "factsheet"},
                                              subject_id=subject, period_end="2026-09-30")

    def test_review_and_released_prefixes_are_removed(self):
        for subject in ("U16088571", "U16088606", "hong-kong-china"):
            with self.subTest(subject=subject):
                plain = subject + "_Factsheet_2026-09-30.pdf"
                self.assertEqual(self.name(SHA_A + "-" + plain, subject), plain)
                self.assertEqual(self.name(SHA_B + "-" + SHA_A + "-" + plain, subject), plain)

    def test_names_without_a_full_hash_prefix_are_unchanged(self):
        for filename in ("U16088571_Factsheet_2026-09-30.pdf", "abc-U16088571_Factsheet_2026-09-30.pdf",
                         SHA_A.upper() + "-U16088571_Factsheet_2026-09-30.pdf", SHA_A + "_report.pdf"):
            with self.subTest(filename=filename):
                self.assertEqual(self.name(filename), filename)

    def test_a_bare_hash_keeps_its_stored_name(self):
        for filename in (SHA_A + "-", SHA_A + "-.pdf"):
            with self.subTest(filename=filename):
                self.assertEqual(self.name(filename), filename)


class _Subject:
    def __init__(self, currency):
        self.currency = currency

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, parameters=()):
        self.parameters = parameters
        row = None if self.currency is None else {"base_currency": self.currency}
        return type("Rows", (), {"fetchone": lambda _self: row})()


class KiwoomUsDownloadNameTests(unittest.TestCase):
    """A Kiwoom account in U.S. dollars downloads as US_Factsheet_<date>, never with its account number."""

    STORED = SHA_A + "-" + SHA_B + "-KIWOOM-0000000000_Factsheet_2026-08-31.pdf"

    def prefix(self, subject, currency):
        with mock.patch.object(web, "_database", lambda: _Subject(currency)):
            return web._download_prefix(subject)

    def test_a_kiwoom_usd_account_is_named_us(self):
        self.assertEqual(self.prefix("KIWOOM-0000000000", "USD"), "US")
        self.assertEqual(web._monthly_download_filename({"filename": self.STORED, "report_type": "factsheet"},
                                                        subject_id="KIWOOM-0000000000", period_end="2026-08-31",
                                                        prefix="US"), "US_Factsheet_2026-08-31.pdf")

    def test_other_accounts_keep_their_names(self):
        self.assertIsNone(self.prefix("KIWOOM-0000000000", "KRW"))
        self.assertIsNone(self.prefix("KIWOOM-0000000000", None))
        self.assertIsNone(self.prefix("U16088571", "USD"))  # IBKR: not looked up
        self.assertEqual(web._monthly_download_filename({"filename": self.STORED, "report_type": "factsheet"},
                                                        subject_id="KIWOOM-0000000000", period_end="2026-08-31"),
                         "KIWOOM-0000000000_Factsheet_2026-08-31.pdf")

    def test_an_unreadable_subject_keeps_the_stored_name(self):
        def broken():
            raise web.PostgresError("permission denied")
        with mock.patch.object(web, "_database", broken):
            self.assertIsNone(web._download_prefix("KIWOOM-0000000000"))

    def test_every_monthly_download_asks_for_the_prefix(self):
        import inspect
        source = inspect.getsource(web)
        self.assertEqual(source.count("_monthly_download_filename(artifact,"), 3)
        self.assertEqual(source.count("prefix=_download_prefix("), 3)


if __name__ == "__main__":
    unittest.main()
