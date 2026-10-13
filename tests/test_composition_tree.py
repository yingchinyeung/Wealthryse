from __future__ import annotations

import unittest
from datetime import date

from ibkr_etl.composition_tree import composite_tree, tree_for_definition, tree_svg
from ibkr_etl.onboarding_v2_web import PortfolioRequest, builder_fields, builder_form_from_definition
from ibkr_etl.subject_onboarding import approved_reference_definitions


class CompositionTreeTests(unittest.TestCase):
    def test_alpha_world_ms_groups_long_short_and_cash(self):
        tree = tree_for_definition(approved_reference_definitions()["alpha-world-ms"])
        self.assertEqual([group["label"] for group in tree["groups"]], ["Long", "Short", "Cash"])
        totals = {key: round(value, 6) for key, value in tree["totals"].items()}
        self.assertEqual(totals, {"nav": 100.0, "long": 136.0, "short": 94.0, "gross": 230.0, "net": 42.0,
                                  "cash": 58.0})
        long_names = [item["name"] for item in tree["groups"][0]["items"]]
        self.assertEqual(long_names, ["China & Hong Kong", "U16088571 (U.S. Technology and Biotechnology)"])
        self.assertEqual(tree["groups"][1]["items"][0]["detail"], "49.0% of NAV · Market index · HKD")
        self.assertEqual(tree["subtitle"], "Fixed-unit composite · HKD · starts at 100")

    def test_borrowed_cash_and_an_impossible_nav(self):
        tree = composite_tree(title="Levered", base_currency="USD", constant_cash=-20,
                              sleeves=[{"name": "A", "kind": "account", "currency": "USD", "coefficient": 1.2}])
        self.assertTrue(tree["groups"][-1]["items"][0]["detail"].endswith("borrowed"))
        with self.assertRaisesRegex(ValueError, "positive"):
            composite_tree(title="Broken", base_currency="USD", constant_cash=-200,
                           sleeves=[{"name": "A", "kind": "account", "currency": "USD", "coefficient": 1}])

    def test_the_drawing_escapes_names(self):
        svg = tree_svg(composite_tree(title="<script>", base_currency="USD", constant_cash=50, sleeves=[
            {"name": "Tom & Jerry <b>", "kind": "portfolio", "currency": "USD", "coefficient": 0.5}]))
        self.assertTrue(svg.startswith("<svg ") and svg.endswith("</svg>"))
        self.assertNotIn("<script>", svg)
        self.assertNotIn("<b>", svg)
        self.assertIn("Tom &amp; Jerry &lt;b&gt;", svg)

    def test_a_copy_of_ms_rebuilds_the_same_sleeves_under_a_new_name(self):
        for subject_id in ("alpha-world-ms", "alpha-world-t"):
            reference = approved_reference_definitions()[subject_id]
            form = builder_form_from_definition(reference)
            self.assertEqual([(s["direction"], s["exposure_percent"]) for s in form["sleeves"]],
                             [("long", 91.0), ("long", 45.0), ("short", 49.0), ("short", 45.0)])
            fields = builder_fields(PortfolioRequest(
                kind=form["kind"], subject_id="alpha-world-copy", display_name="Alpha World copy",
                reporting_currency=form["reporting_currency"], start_date=date.fromisoformat(form["start_date"]),
                sleeves=form["sleeves"]))
            self.assertEqual(fields["constant_cash"], reference["constant_cash"])
            self.assertEqual(fields["normalization_date"], reference["normalization_date"])
            self.assertEqual([(c["component_id"], c["coefficient"]) for c in fields["components"]],
                             [(c["component_id"], c["coefficient"]) for c in reference["components"]])


if __name__ == "__main__":
    unittest.main()
