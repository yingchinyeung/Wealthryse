"""Download names drop the content-address prefix of stored report files."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
