"""The website-design workflow's page redesign modes: each phase reaches owners first."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_release_pages", ROOT / "deploy" / "website_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class NextPagesValueTest(unittest.TestCase):
    def test_preview_keeps_everyone_where_they_are_and_gives_owners_the_latest(self):
        self.assertEqual(release.next_pages_value("pages-preview", "off", 2), "0/2")
        self.assertEqual(release.next_pages_value("pages-preview", "on", 2), "1/2")
        self.assertEqual(release.next_pages_value("pages-preview", "1/2", 3), "1/3")
        self.assertEqual(release.next_pages_value("pages-preview", "2", 3), "2/3")
        self.assertEqual(release.next_pages_value("pages-preview", None, 1), "0/1")

    def test_preview_of_what_everyone_already_has_changes_nothing(self):
        self.assertEqual(release.next_pages_value("pages-preview", "2", 2), "2")

    def test_on_and_off(self):
        self.assertEqual(release.next_pages_value("pages-on", "1/2", 2), "2")
        self.assertEqual(release.next_pages_value("pages-off", "2", 2), "off")

    def test_latest_matches_the_website_code(self):
        from ibkr_etl import web_next
        self.assertEqual(release.next_pages_latest(), web_next.PAGES_PHASES)


if __name__ == "__main__":
    unittest.main()
