"""The release ships deploy/live-merged/web.py; check the trade changes are in it.

The live copy imports modules that are not in this repo, so it cannot be loaded
here. Instead its trade_history must match the tested src version exactly.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_WEB = ROOT / "deploy/live-merged/src/ibkr_etl/web.py"
SRC_WEB = ROOT / "src/ibkr_etl/web.py"


def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.dump(node, include_attributes=False)
    raise AssertionError(f"{name} not found in {path}")


class LiveMergedTradeTests(unittest.TestCase):
    def test_trade_history_matches_tested_src_version(self):
        self.assertEqual(_function(LIVE_WEB, "trade_history"), _function(SRC_WEB, "trade_history"))

    def test_filtered_portfolios_use_the_filtered_trade_query(self):
        source = LIVE_WEB.read_text(encoding="utf-8")
        self.assertIn('if subject_type == "composite_portfolio":', source)
        self.assertIn("from .subject_exports import filtered_trade_query", source)
        self.assertNotIn("A derived portfolio has no independently executed trades", source)

    def test_data_export_page_has_trade_csv_button(self):
        source = LIVE_WEB.read_text(encoding="utf-8")
        self.assertIn('<a id="export-trades-csv" class="button" href="#">Trade history CSV</a>', source)
        self.assertIn('{trade_export_link}<a class="button" href="/accounts/{account}/trades">', source)
        self.assertIn("export/trades.csv?as_of=", source)


if __name__ == "__main__":
    unittest.main()
