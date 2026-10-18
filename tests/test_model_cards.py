from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import model_cards  # noqa: E402

QUANT = ROOT / "src" / "ibkr_etl" / "quant"


class ModelCardTests(unittest.TestCase):
    def test_every_card_matches_its_code(self) -> None:
        for model_id, item in model_cards.cards().items():
            with self.subTest(model_id):
                module = importlib.import_module(f"ibkr_etl.{item.module}")
                self.assertIsInstance(getattr(module, item.version_constant), str)
                for name in item.entry_points:
                    self.assertTrue(callable(getattr(module, name, None)), name)
                self.assertIn(item.status, model_cards.STATUSES)
                self.assertTrue(set(item.served_on) <= set(model_cards.SERVED_ON), item.served_on)
                self.assertTrue(item.limits, "every model states at least one limit")
                for path in re.findall(r"tests/test_\w+\.py", item.validation):
                    self.assertTrue((ROOT / path).is_file(), path)

    def test_every_quant_engine_has_a_card(self) -> None:
        carded = {item.module for item in model_cards.cards().values()}
        engines = {f"quant.{path.stem}" for path in QUANT.glob("*.py") if path.stem != "__init__"}
        self.assertEqual(sorted(engines - carded), [], "add a card to model_cards.py")

    def test_research_models_are_never_served(self) -> None:
        for item in model_cards.cards().values():
            if item.status == "research":
                self.assertEqual(item.served_on, ("none",), item.model_id)

    def test_as_dict_reports_the_live_version(self) -> None:
        payload = model_cards.as_dict("volatility-forecast")
        from ibkr_etl import risk_forecasting
        self.assertEqual(payload["version"], risk_forecasting.MODEL_VERSION)
        with self.assertRaises(KeyError):
            model_cards.card("nope")


if __name__ == "__main__":
    unittest.main()
