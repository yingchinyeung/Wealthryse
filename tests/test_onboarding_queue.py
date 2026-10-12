from __future__ import annotations

import unittest
from pathlib import Path

from ibkr_etl.onboarding_queue import (
    QueueAccount,
    classify_connection_accounts,
    initial_queue_items,
    next_queue_state,
    normalize_account_order,
)


class AccountOnboardingQueueTests(unittest.TestCase):
    def test_order_is_discovered_unique_and_bounded(self) -> None:
        self.assertEqual(
            normalize_account_order(
                ["u16088606", "U16088571"],
                discovered_account_ids=["U16088571", "U16088606"],
            ),
            ("U16088606", "U16088571"),
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalize_account_order(
                ["U16088606", "U16088606"],
                discovered_account_ids=["U16088606"],
            )
        with self.assertRaisesRegex(ValueError, "not discovered"):
            normalize_account_order(
                ["U99999999"], discovered_account_ids=["U16088606"]
            )

    def test_only_first_selected_account_starts(self) -> None:
        items = initial_queue_items(("U16088606", "U16088571", "U19025068"))
        self.assertEqual([item.state for item in items], ["current", "queued", "queued"])
        self.assertEqual([item.position for item in items], [1, 2, 3])

    def test_next_account_cannot_start_before_current_is_active(self) -> None:
        items = (
            QueueAccount("U16088606", 1, "current", "in_progress"),
            QueueAccount("U16088571", 2, "queued"),
        )
        with self.assertRaisesRegex(ValueError, "completely activated"):
            next_queue_state(items)

    def test_activation_unlocks_exactly_one_next_account(self) -> None:
        items = (
            QueueAccount("U16088606", 1, "current", "active"),
            QueueAccount("U16088571", 2, "queued"),
            QueueAccount("U19025068", 3, "queued"),
        )
        advanced = next_queue_state(items)
        self.assertEqual([item.state for item in advanced], ["completed", "current", "queued"])

    def test_unselected_and_deferred_accounts_remain_available_later(self) -> None:
        rows = classify_connection_accounts(
            [
                {"source_account_id": "U1"},
                {"source_account_id": "U2"},
                {"source_account_id": "U3"},
            ],
            [
                {"source_account_id": "U1", "item_status": "current"},
                {"source_account_id": "U2", "item_status": "deferred"},
            ],
        )
        self.assertEqual(
            [row["onboarding_group"] for row in rows],
            ["onboarding_now", "available_later", "available_later"],
        )

    def test_migration_enforces_one_queue_and_one_current_account(self) -> None:
        migration = Path(
            "migrations/postgres/035_connection_onboarding_queue.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("account_onboarding_queues_open_source_idx", migration)
        self.assertIn("account_onboarding_queue_one_current_idx", migration)
        self.assertIn("WHERE item_status = 'current'", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)

    def test_web_exposes_queue_builder_start_and_activation_gated_advance(self) -> None:
        web = Path("src/ibkr_etl/web.py").read_text(encoding="utf-8")
        self.assertIn(
            '"/account-onboarding/connections/{flex_source_id}/accounts"', web
        )
        self.assertIn(
            '"/api/onboarding/flex-sources/{flex_source_id}/queue/current/start"',
            web,
        )
        self.assertIn(
            '"/api/onboarding/flex-sources/{flex_source_id}/queue/advance"', web
        )
        self.assertIn("previous_account_fully_active", web)
        self.assertIn("retained_for_future_onboarding", web)

    def test_write_role_has_least_privilege_for_queue_tables(self) -> None:
        bootstrap = Path("scripts/bootstrap_postgres.py").read_text(encoding="utf-8")
        self.assertIn("account_onboarding_queues", bootstrap)
        self.assertIn("account_onboarding_queue_items", bootstrap)
        self.assertNotIn(
            "GRANT ALL ON TABLE account_onboarding_queues", bootstrap
        )


if __name__ == "__main__":
    unittest.main()
