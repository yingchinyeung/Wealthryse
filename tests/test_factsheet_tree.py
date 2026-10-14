import unittest

from ibkr_etl import factsheet_tree as ft
from ibkr_etl.composition_tree import composite_tree
from ibkr_etl.report_profiles import ReportProfileError, check_style

from test_report_profiles import composite


def tree(sleeves, cash=58.0):
    return composite_tree(title="Test", base_currency="HKD", constant_cash=cash, sleeves=sleeves)


MS_LIKE = [
    {"name": "China & Hong Kong Portfolio", "kind": "portfolio", "currency": "USD", "coefficient": .91},
    {"name": "US Technology & BioTechnology", "kind": "account", "currency": "USD", "coefficient": .45},
    {"name": "Hang Seng Index", "kind": "benchmark", "currency": "HKD", "coefficient": -.49},
    {"name": "S&P500", "kind": "benchmark", "currency": "USD", "coefficient": -.45},
]


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class TreeLayoutTests(unittest.TestCase):
    def test_groups_and_sleeves_stay_inside_the_allocation_area_without_overlap(self):
        for sleeves in (MS_LIKE, MS_LIKE[:1], [
                {"name": f"Market {i}", "kind": "account", "currency": "USD", "coefficient": .2 if i % 2 else -.1}
                for i in range(7)]):
            with self.subTest(count=len(sleeves)):
                shapes = ft.layout(tree(sleeves))
                x, y, w, h = ft.AREA
                for box in shapes.boxes:
                    self.assertTrue(x <= box[0] and box[0] + box[2] <= x + w and y <= box[1]
                                    and box[1] + box[3] <= y + h, box)
                for i, a in enumerate(shapes.boxes):
                    for b in shapes.boxes[i + 1:]:
                        self.assertFalse(overlaps(a, b), (a, b))
                self.assertTrue(all(text.size >= ft.MINIMUM_FONT for text in shapes.texts))
                self.assertEqual(len(shapes.lines), len(sleeves) + 1)

    def test_ms_composition_reads_as_its_long_short_and_cash_shares(self):
        texts = [text.value for text in ft.layout(tree(MS_LIKE)).texts]
        for expected in ("LONG 136%", "SHORT 94%", "CASH 58%", "91%", "49%", "45%",
                         "Hang Seng", "Index", "S&P500"):
            self.assertIn(expected, texts)
        self.assertEqual(ft.percent(116.6667), "116.7%")

    def test_a_composition_that_cannot_be_drawn_is_refused_before_onboarding(self):
        crowded = [{"name": f"Sleeve number {i} with a long name", "kind": "account", "currency": "USD",
                    "coefficient": .1} for i in range(9)]
        with self.assertRaises(ValueError):
            ft.layout(tree(crowded))
        definition = composite(components=[
            {"component_id": f"sleeve-{i}", "component_name": f"Sleeve number {i} with a long name",
             "component_type": "account", "coefficient": .1, "source_currency": "USD", "price_convention": "nav"}
            for i in range(9)])
        with self.assertRaisesRegex(ReportProfileError, "shorter sleeve names"):
            check_style(definition, "alpha-world-ms")


if __name__ == "__main__":
    unittest.main()
