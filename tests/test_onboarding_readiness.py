"""The go-live readiness pack's pure parts: the terms comparison and what go-live switches on. Synthetic values."""
from __future__ import annotations

import unittest

from ibkr_etl.onboarding_readiness import go_live_effects, terms_comparison

APPROVED = {"display_name": "Synthetic Growth", "reporting_currency": "HKD", "history_start_date": "2025-01-02"}


def draft(**values):
    return {"terms": {key: {"value": value, "sources": [{"document": "mandate.pdf", "page": 1, "quote": "q"}]}
                      for key, value in values.items()}}


class TermsComparisonTests(unittest.TestCase):
    def test_each_term_says_whether_the_documents_agree(self):
        rows = {r["term"]: r for r in terms_comparison(
            APPROVED, draft(display_name="synthetic growth", reporting_currency="USD"))}
        self.assertEqual(rows["display_name"]["state"], "matches")  # case does not matter
        self.assertEqual(rows["reporting_currency"]["state"], "differs")
        self.assertEqual(rows["reporting_currency"]["documents"], "USD")
        self.assertEqual(rows["reporting_currency"]["source"]["document"], "mandate.pdf")
        self.assertEqual(rows["history_start_date"]["state"], "not_in_documents")

    def test_without_a_draft_every_term_says_so(self):
        self.assertEqual({r["state"] for r in terms_comparison(APPROVED, None)}, {"no_draft"})


class GoLiveEffectsTests(unittest.TestCase):
    def test_a_broker_account_lists_its_data_feed_and_never_sends_anything(self):
        effects = go_live_effects({"source_kind": "broker_account", "broker": "ibkr_flex",
                                   "history_start_date": "2025-01-02"})
        self.assertIn("Daily data from IBKR FLEX from 2025-01-02", effects)
        self.assertIn("Its broker data source and scheduled download switch on.", effects)
        self.assertTrue(effects[-1].startswith("Nothing is sent to anyone"))

    def test_a_derived_portfolio_is_calculated_not_downloaded(self):
        effects = go_live_effects({"source_kind": "composite_portfolio", "broker": None,
                                   "history_start_date": "2025-01-02"})
        self.assertIn("Daily data from the derived calculation from 2025-01-02", effects)
        self.assertNotIn("Its broker data source and scheduled download switch on.", effects)


if __name__ == "__main__":
    unittest.main()
